"""
AI 客户端模块 - AlphaPai会议纪要标签判断
提供统一的 AI 调用接口，支持三种场景类型：
- tag_judgment: 标签判断型（精准快速）
- short_text: 简短文字型（平衡）
- long_thinking: 长篇思考型（质量优先）

支持三种AI提供商：
- modelscope: ModelScope平台的多模型系统
- huoshan: 火山引擎的kimi-k2.5模型
- minimax: MiniMax官方API (Minimax-2.7)
- zhongxin: 中信 AI 网关（OpenAI Chat Completions 兼容）

线程安全：支持多线程并行调用
"""
from __future__ import annotations

import json
import os
import time
import re
import threading
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from investment_system.common.ai.quality import clean_thinking_tags
from investment_system.common.storage.download_history import save_json_atomic


class AICallType(Enum):
    """AI 调用类型枚举"""
    TAG_JUDGMENT = "tag_judgment"
    SHORT_TEXT = "short_text"
    LONG_THINKING = "long_thinking"


class AIModelError(Exception):
    """AI 模型调用错误"""
    pass


class AIAllModelsFailedError(Exception):
    """所有模型都失败"""
    pass


class AIQuotaExhaustedError(Exception):
    """所有模型配额耗尽"""
    pass


# 项目根目录
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent
DATA_CONFIG_DIR = PROJECT_ROOT / "data" / "config"
AI_MODELS_CONFIG_FILE = DATA_CONFIG_DIR / "ai_models.json"
API_KEYS_FILE = PROJECT_ROOT / "data" / "credentials" / "AI_api_keys.txt"

# 火山引擎固定使用的模型
HUOSHAN_MODEL = "kimi-k2.5"

# MiniMax 默认模型（可从配置文件中选择）
MINIMAX_MODEL_DEFAULT = "MiniMax-M2.7"

# 中信 AI 默认模型（可从配置文件中选择）
ZHONGXIN_MODEL_DEFAULT = "DeepSeek-V4-Pro"

# 冷却器参数
COOLDOWN_THRESHOLD = 3  # 连续429次数阈值
COOLDOWN_WINDOW_SECONDS = 300  # 冷却时间窗口（秒）
MAX_TOTAL_ATTEMPTS = 50  # 无限循环防护：最大总尝试次数


def _today_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_time(iso_str: Optional[str]) -> Optional[datetime]:
    """解析ISO时间字符串"""
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
    except:
        return None


def _load_api_keys() -> Dict[str, str]:
    """
    从 AI_api_keys.txt 加载所有 API Key
    
    文件格式：
    huoshan: xxx
    modelscope: xxx
    minimax: xxx
    zhongxin: xxx
    
    Returns:
        Dict[provider, api_key]
    """
    keys = {}
    
    # 先从环境变量读取
    for provider in ["huoshan", "modelscope", "minimax", "zhongxin"]:
        env_key = os.environ.get(f"{provider.upper()}_API_KEY")
        if env_key:
            keys[provider] = env_key
    if "zhongxin" not in keys and os.environ.get("ANTHROPIC_API_KEY"):
        keys["zhongxin"] = os.environ["ANTHROPIC_API_KEY"]
    
    # 再从文件读取
    if API_KEYS_FILE.exists():
        content = API_KEYS_FILE.read_text(encoding="utf-8").strip()
        for line in content.split("\n"):
            line = line.strip()
            if not line or ":" not in line:
                continue
            parts = line.split(":", 1)
            if len(parts) == 2:
                provider = parts[0].strip().lower()
                key = parts[1].strip()
                if provider and key:
                    keys[provider] = key
    
    return keys


def _get_api_key(provider: str) -> str:
    """
    获取指定提供商的 API Key
    
    Args:
        provider: "modelscope", "huoshan"、"minimax" 或 "zhongxin"
    
    Returns:
        API Key
    
    Raises:
        RuntimeError: 如果找不到 API Key
    """
    keys = _load_api_keys()
    
    if provider in keys:
        return keys[provider]
    
    raise RuntimeError(
        f"未找到 {provider} 的 API Key。"
        f"请在 {API_KEYS_FILE} 中添加配置，格式为: {provider}: your_api_key"
    )


class AIClient:
    """
    AI 客户端
    支持三种场景类型的模型调用，自动管理模型顺序、配额和统计
    支持四种AI提供商：modelscope、huoshan、minimax 和 zhongxin
    
    线程安全：使用类级别可重入锁(RLock)保护配置文件读写和配额管理
    RLock允许同一线程多次获取锁，避免嵌套调用时的死锁问题
    """
    
    _config_lock = threading.RLock()  # 可重入锁，避免嵌套调用死锁
    _log_lock = threading.Lock()
    
    def __init__(self, log_func: Optional[Callable[[str, str], None]] = None, provider: Optional[str] = None):
        """
        初始化 AI 客户端
        
        Args:
            log_func: 日志函数，签名为 (message: str, level: str) -> None
            provider: AI提供商，"modelscope"、"huoshan"、"minimax" 或 "zhongxin"，默认从配置文件读取
        """
        self._log_func = log_func or (lambda msg, level: print(f"[{level}] {msg}"))
        self._config = self._load_config()
        
        # 确定使用哪个提供商
        self._provider = provider or self._config.get("ai_provider", "modelscope")
        
        # 根据提供商加载对应的 API Key
        self._api_key = _get_api_key(self._provider)
        
        if self._provider == "huoshan":
            self._log(f"使用火山引擎 AI 提供商 (模型: {HUOSHAN_MODEL})", "INFO")
        elif self._provider == "minimax":
            self._log(f"使用 MiniMax AI 提供商 (模型: {self._get_minimax_model()})", "INFO")
        elif self._provider == "zhongxin":
            self._log(f"使用中信 AI 提供商 (模型: {self._get_zhongxin_model()})", "INFO")
        else:
            self._log(f"使用 ModelScope AI 提供商", "INFO")
    
    def _log(self, msg: str, level: str = "INFO"):
        """记录日志（线程安全）"""
        with AIClient._log_lock:
            self._log_func(msg, level)
    
    def _load_config(self) -> Dict[str, Any]:
        """加载配置文件"""
        if not AI_MODELS_CONFIG_FILE.exists():
            raise RuntimeError(f"未找到 AI 模型配置文件：{AI_MODELS_CONFIG_FILE}")
        
        with open(AI_MODELS_CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
        
        # 检查日期，如果是新的一天，重置每日统计
        today = _today_str()
        if config.get("last_updated") != today:
            config["last_updated"] = today
            # 重置 ModelScope 模型统计
            for model_id, model_info in config.get("modelscope_models", {}).items():
                model_info["daily_used"] = 0
                model_info["daily_remaining"] = model_info.get("daily_limit", 10)
                model_info["avg_duration_today"] = None
                if "error_counts" in model_info:
                    for error_type in model_info["error_counts"]:
                        model_info["error_counts"][error_type] = 0
                # 重置冷却器相关字段
                model_info["consecutive_rate_limit_count"] = 0
                model_info["last_call_time"] = None
            # 重置火山引擎模型统计
            for model_id, model_info in config.get("huoshan_models", {}).items():
                model_info["daily_used"] = 0
                model_info["daily_remaining"] = model_info.get("daily_limit", 1000)
                model_info["avg_duration_today"] = None
                if "error_counts" in model_info:
                    for error_type in model_info["error_counts"]:
                        model_info["error_counts"][error_type] = 0
                model_info["consecutive_rate_limit_count"] = 0
                model_info["last_call_time"] = None
            # 重置 MiniMax 模型统计
            for model_id, model_info in config.get("minimax_models", {}).items():
                model_info["daily_used"] = 0
                model_info["daily_remaining"] = model_info.get("daily_limit", 1000)
                model_info["avg_duration_today"] = None
                if "error_counts" in model_info:
                    for error_type in model_info["error_counts"]:
                        model_info["error_counts"][error_type] = 0
                model_info["consecutive_rate_limit_count"] = 0
                model_info["last_call_time"] = None
            # 重置中信 AI 模型统计
            for model_id, model_info in config.get("zhongxin_models", {}).items():
                model_info["daily_used"] = 0
                model_info["daily_remaining"] = model_info.get("daily_limit", 1000)
                model_info["avg_duration_today"] = None
                if "error_counts" in model_info:
                    for error_type in model_info["error_counts"]:
                        model_info["error_counts"][error_type] = 0
                model_info["consecutive_rate_limit_count"] = 0
                model_info["last_call_time"] = None
            self._save_config(config)
        
        # 确保所有模型都有冷却器相关字段（兼容旧配置）
        for model_id, model_info in config.get("modelscope_models", {}).items():
            if "consecutive_rate_limit_count" not in model_info:
                model_info["consecutive_rate_limit_count"] = 0
            if "last_call_time" not in model_info:
                model_info["last_call_time"] = None
        for model_id, model_info in config.get("huoshan_models", {}).items():
            if "consecutive_rate_limit_count" not in model_info:
                model_info["consecutive_rate_limit_count"] = 0
            if "last_call_time" not in model_info:
                model_info["last_call_time"] = None
        for model_id, model_info in config.get("minimax_models", {}).items():
            if "consecutive_rate_limit_count" not in model_info:
                model_info["consecutive_rate_limit_count"] = 0
            if "last_call_time" not in model_info:
                model_info["last_call_time"] = None
        for model_id, model_info in config.get("zhongxin_models", {}).items():
            if "consecutive_rate_limit_count" not in model_info:
                model_info["consecutive_rate_limit_count"] = 0
            if "last_call_time" not in model_info:
                model_info["last_call_time"] = None
        
        return config
    
    def _save_config(self, config: Dict[str, Any]):
        """保存配置文件（线程安全，带重试和原子写入保护）"""
        with AIClient._config_lock:
            max_retries = 3
            retry_delay = 0.1
            
            for attempt in range(max_retries):
                try:
                    save_json_atomic(config, AI_MODELS_CONFIG_FILE)
                    return
                    
                except (IOError, OSError) as e:
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        continue
                    self._log(f"保存 AI 模型配置失败: {e}", "WARN")
                except Exception as e:
                    self._log(f"保存 AI 模型配置失败: {e}", "WARN")
                    return
    
    def _get_minimax_model(self) -> str:
        """获取当前配置的 MiniMax 模型"""
        return self._config.get("minimax_default_model", MINIMAX_MODEL_DEFAULT)

    def _get_zhongxin_model(self) -> str:
        """获取当前配置的中信 AI 模型"""
        return self._config.get("zhongxin_default_model", ZHONGXIN_MODEL_DEFAULT)
    
    def _get_model_order(self, call_type: AICallType) -> List[str]:
        """获取指定场景类型的模型顺序"""
        if self._provider == "huoshan":
            # 火山引擎只使用一个固定模型
            return [HUOSHAN_MODEL]
        
        if self._provider == "minimax":
            # MiniMax 使用配置中选择的模型
            return [self._get_minimax_model()]
        if self._provider == "zhongxin":
            # 中信 AI 使用配置中选择的模型
            return [self._get_zhongxin_model()]
        
        # ModelScope 使用配置的模型顺序
        order = self._config.get("modelscope_model_order", {}).get(call_type.value, [])
        if not order:
            return list(self._config.get("modelscope_models", {}).keys())
        return order
    
    def _get_default_params(self, call_type: AICallType) -> Dict[str, Any]:
        """获取指定场景类型的默认参数"""
        return self._config.get("default_params", {}).get(call_type.value, {
            "temperature": 0.4,
            "max_tokens": 128
        })
    
    def _get_api_config(self) -> Dict[str, Any]:
        """获取 API 配置"""
        if self._provider == "huoshan":
            return self._config.get("huoshan_api_config", {
                "base_url": "https://ark.cn-beijing.volces.com/api/coding/v3",
                "timeout_seconds": 240,
                "max_retries": 3,
                "retry_delay_seconds": 10
            })
        elif self._provider == "minimax":
            return self._config.get("minimax_api_config", {
                "base_url": "https://api.minimaxi.com/v1",
                "timeout_seconds": 240,
                "max_retries": 3,
                "retry_delay_seconds": 10
            })
        elif self._provider == "zhongxin":
            config = dict(self._config.get("zhongxin_api_config", {
                "base_url": "http://ai-api.citicsinfo.com/v1/chat/completions",
                "timeout_seconds": 240,
                "max_retries": 3,
                "retry_delay_seconds": 10
            }))
            env_base_url = os.environ.get("ZHONGXIN_BASE_URL") or os.environ.get("ANTHROPIC_BASE_URL")
            if env_base_url:
                config["base_url"] = env_base_url
            return config
        else:
            return self._config.get("modelscope_api_config", {
                "base_url": "https://api-inference.modelscope.cn/v1",
                "timeout_seconds": 240,
                "max_retries": 3,
                "retry_delay_seconds": 10
            })
    
    def _get_models_config(self) -> Dict[str, Any]:
        """获取当前提供商的模型配置"""
        if self._provider == "huoshan":
            return self._config.get("huoshan_models", {})
        elif self._provider == "minimax":
            return self._config.get("minimax_models", {})
        elif self._provider == "zhongxin":
            return self._config.get("zhongxin_models", {})
        else:
            return self._config.get("modelscope_models", {})
    
    def _is_model_in_cooldown(self, model: str) -> bool:
        """
        检查模型是否处于冷却期（仅 ModelScope，线程安全）
        条件：连续3次429 + 最近一次调用在300秒内
        """
        if self._provider != "modelscope":
            return False
        
        with AIClient._config_lock:
            models_config = self._get_models_config()
            model_info = models_config.get(model, {})
            
            consecutive_count = model_info.get("consecutive_rate_limit_count", 0)
            last_call_time_str = model_info.get("last_call_time")
            
            if consecutive_count < COOLDOWN_THRESHOLD:
                return False
            
            if not last_call_time_str:
                return False
            
            last_call_time = _parse_iso_time(last_call_time_str)
            if not last_call_time:
                return False
            
            now = datetime.now(timezone.utc)
            seconds_since_last_call = (now - last_call_time).total_seconds()
            
            return seconds_since_last_call < COOLDOWN_WINDOW_SECONDS
    
    def _update_rate_limit_cooldown(self, model: str, is_rate_limited: bool):
        """
        更新模型的速率限制冷却计数（仅 ModelScope，线程安全）
        
        Args:
            model: 模型ID
            is_rate_limited: 是否遇到速率限制
        """
        if self._provider != "modelscope":
            return
        
        with AIClient._config_lock:
            models_config = self._get_models_config()
            if model not in models_config:
                return
            
            model_info = models_config[model]
            
            # 更新最后调用时间
            model_info["last_call_time"] = _now_iso()
            
            if is_rate_limited:
                # 增加连续429计数
                current_count = model_info.get("consecutive_rate_limit_count", 0)
                model_info["consecutive_rate_limit_count"] = current_count + 1
            else:
                # 重置计数（成功或其他错误都重置）
                model_info["consecutive_rate_limit_count"] = 0
            
            self._save_config(self._config)
    
    def _call_api_modelscope(
        self, 
        model: str, 
        prompt: str, 
        temperature: float, 
        max_tokens: int
    ) -> Tuple[str, Dict[str, Optional[str]], float]:
        """调用 ModelScope API"""
        api_config = self._get_api_config()
        url = self._chat_completions_url(api_config)
        
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        start_time = time.perf_counter()
        
        try:
            resp = requests.post(
                url, 
                headers=headers, 
                data=json.dumps(payload), 
                timeout=api_config.get("timeout_seconds", 120)
            )
        except requests.exceptions.Timeout:
            duration = time.perf_counter() - start_time
            raise AIModelError(f"请求超时（{duration:.1f}秒）")
        except Exception as e:
            raise AIModelError(f"请求异常: {e}")
        
        duration = time.perf_counter() - start_time
        
        if resp.status_code == 200:
            data = resp.json()
            rate_headers = {
                "user_limit": resp.headers.get("modelscope-ratelimit-requests-limit"),
                "user_remaining": resp.headers.get("modelscope-ratelimit-requests-remaining"),
                "model_limit": resp.headers.get("modelscope-ratelimit-model-requests-limit"),
                "model_remaining": resp.headers.get("modelscope-ratelimit-model-requests-remaining"),
            }
            
            choice0 = (data.get("choices") or [{}])[0] or {}
            msg0 = choice0.get("message") or {}
            content = (msg0.get("content") or "").strip()
            
            if not content:
                raise AIModelError("模型返回内容为空")
            
            return content, rate_headers, duration
        else:
            text = resp.text
            is_rate_limited = (
                resp.status_code == 429
                or "速率限制" in text
                or "rate limit" in text.lower()
                or 'code":"1302' in text
            )
            if is_rate_limited:
                raise AIModelError(f"速率限制: HTTP {resp.status_code}")
            else:
                raise AIModelError(f"HTTP {resp.status_code}: {text[:200]}")
    
    def _call_api_huoshan(
        self, 
        model: str, 
        prompt: str, 
        temperature: float, 
        max_tokens: int
    ) -> Tuple[str, Dict[str, Optional[str]], float]:
        """调用火山引擎 API"""
        api_config = self._get_api_config()
        url = self._chat_completions_url(api_config)
        
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        start_time = time.perf_counter()
        
        try:
            resp = requests.post(
                url, 
                headers=headers, 
                json=payload, 
                timeout=api_config.get("timeout_seconds", 120)
            )
        except requests.exceptions.Timeout:
            duration = time.perf_counter() - start_time
            raise AIModelError(f"请求超时（{duration:.1f}秒）")
        except Exception as e:
            raise AIModelError(f"请求异常: {e}")
        
        duration = time.perf_counter() - start_time
        
        if resp.status_code == 200:
            data = resp.json()
            rate_headers = {}  # 火山引擎没有特定的速率限制头
            
            choice0 = (data.get("choices") or [{}])[0] or {}
            msg0 = choice0.get("message") or {}
            content = (msg0.get("content") or "").strip()
            
            if not content:
                raise AIModelError("模型返回内容为空")
            
            return content, rate_headers, duration
        else:
            text = resp.text
            is_rate_limited = (
                resp.status_code == 429
                or "速率限制" in text
                or "rate limit" in text.lower()
                or "频率" in text
            )
            if is_rate_limited:
                raise AIModelError(f"速率限制: HTTP {resp.status_code}")
            else:
                raise AIModelError(f"HTTP {resp.status_code}: {text[:200]}")
    
    def _call_api_minimax(
        self, 
        model: str, 
        prompt: str, 
        temperature: float, 
        max_tokens: int
    ) -> Tuple[str, Dict[str, Optional[str]], float]:
        """调用 MiniMax API (OpenAI 兼容格式)"""
        api_config = self._get_api_config()
        url = self._chat_completions_url(api_config)
        
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        start_time = time.perf_counter()
        
        try:
            resp = requests.post(
                url, 
                headers=headers, 
                json=payload, 
                timeout=api_config.get("timeout_seconds", 240)
            )
        except requests.exceptions.Timeout:
            duration = time.perf_counter() - start_time
            raise AIModelError(f"请求超时（{duration:.1f}秒）")
        except Exception as e:
            raise AIModelError(f"请求异常: {e}")
        
        duration = time.perf_counter() - start_time
        
        if resp.status_code == 200:
            data = resp.json()
            rate_headers = {
                "usage_prompt_tokens": str((data.get("usage") or {}).get("prompt_tokens", "")),
                "usage_completion_tokens": str((data.get("usage") or {}).get("completion_tokens", "")),
                "usage_total_tokens": str((data.get("usage") or {}).get("total_tokens", "")),
            }
            for key, value in resp.headers.items():
                lowered = key.lower()
                if "rate" in lowered or "limit" in lowered or "quota" in lowered or "remain" in lowered:
                    rate_headers[lowered] = value
            
            choice0 = (data.get("choices") or [{}])[0] or {}
            msg0 = choice0.get("message") or {}
            content = (msg0.get("content") or "").strip()
            
            if not content:
                raise AIModelError("模型返回内容为空")
            
            return content, rate_headers, duration
        else:
            text = resp.text
            is_rate_limited = (
                resp.status_code == 429
                or "速率限制" in text
                or "rate limit" in text.lower()
                or "频率" in text
            )
            if is_rate_limited:
                raise AIModelError(f"速率限制: HTTP {resp.status_code}")
            else:
                raise AIModelError(f"HTTP {resp.status_code}: {text[:200]}")

    def _call_api_zhongxin(
        self,
        model: str,
        prompt: str,
        temperature: float,
        max_tokens: int
    ) -> Tuple[str, Dict[str, Optional[str]], float]:
        """调用中信 AI API (OpenAI Chat Completions 兼容格式)"""
        api_config = self._get_api_config()
        url = self._chat_completions_url(api_config)

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        start_time = time.perf_counter()

        try:
            resp = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=api_config.get("timeout_seconds", 240)
            )
        except requests.exceptions.Timeout:
            duration = time.perf_counter() - start_time
            raise AIModelError(f"请求超时（{duration:.1f}秒）")
        except Exception as e:
            raise AIModelError(f"请求异常: {e}")

        duration = time.perf_counter() - start_time

        if resp.status_code == 200:
            data = resp.json()
            rate_headers = {
                "usage_prompt_tokens": str((data.get("usage") or {}).get("prompt_tokens", "")),
                "usage_completion_tokens": str((data.get("usage") or {}).get("completion_tokens", "")),
                "usage_total_tokens": str((data.get("usage") or {}).get("total_tokens", "")),
            }
            for key, value in resp.headers.items():
                lowered = key.lower()
                if "rate" in lowered or "limit" in lowered or "quota" in lowered or "remain" in lowered:
                    rate_headers[lowered] = value

            choice0 = (data.get("choices") or [{}])[0] or {}
            msg0 = choice0.get("message") or {}
            content = (msg0.get("content") or "").strip()

            if not content:
                raise AIModelError("模型返回内容为空")

            return content, rate_headers, duration

        text = resp.text
        is_rate_limited = (
            resp.status_code == 429
            or "速率限制" in text
            or "rate limit" in text.lower()
            or "频率" in text
        )
        if is_rate_limited:
            raise AIModelError(f"速率限制: HTTP {resp.status_code}")
        raise AIModelError(f"HTTP {resp.status_code}: {text[:200]}")

    def _chat_completions_url(self, api_config: Dict[str, Any]) -> str:
        """兼容 base_url 写到 /v1 或直接写到 /chat/completions 两种配置。"""
        base_url = str(api_config.get("base_url", "")).rstrip("/")
        if base_url.endswith("/chat/completions"):
            return base_url
        return f"{base_url}/chat/completions"
    
    def _call_api(
        self, 
        model: str, 
        prompt: str, 
        temperature: float, 
        max_tokens: int
    ) -> Tuple[str, Dict[str, Optional[str]], float]:
        """调用 API（根据提供商选择）"""
        if self._provider == "huoshan":
            content, rate_headers, duration = self._call_api_huoshan(model, prompt, temperature, max_tokens)
        elif self._provider == "minimax":
            content, rate_headers, duration = self._call_api_minimax(model, prompt, temperature, max_tokens)
        elif self._provider == "zhongxin":
            content, rate_headers, duration = self._call_api_zhongxin(model, prompt, temperature, max_tokens)
        else:
            content, rate_headers, duration = self._call_api_modelscope(model, prompt, temperature, max_tokens)

        cleaned = clean_thinking_tags(content)
        if not cleaned:
            raise AIModelError("模型返回内容为空")
        return cleaned, rate_headers, duration
    
    def _update_model_stats(
        self, 
        model: str, 
        call_type: AICallType,
        success: bool,
        duration_seconds: Optional[float] = None,
        rate_headers: Optional[Dict[str, Optional[str]]] = None,
        error_type: Optional[str] = None
    ):
        """更新模型统计数据（线程安全）"""
        with AIClient._config_lock:
            models = self._get_models_config()
            if model not in models:
                return
            
            model_info = models[model]
            
            # 更新最后调用时间
            model_info["last_call_time"] = _now_iso()
            
            # 更新调用次数
            model_info["total_calls"] = model_info.get("total_calls", 0) + 1
            model_info["daily_used"] = model_info.get("daily_used", 0) + 1
            
            calls_by_type = model_info.get("calls_by_type", {})
            calls_by_type[call_type.value] = calls_by_type.get(call_type.value, 0) + 1
            model_info["calls_by_type"] = calls_by_type
            
            if success:
                # 成功时重置冷却计数
                model_info["consecutive_rate_limit_count"] = 0
                
                # 更新成功次数
                success_by_type = model_info.get("success_by_type", {})
                success_by_type[call_type.value] = success_by_type.get(call_type.value, 0) + 1
                model_info["success_by_type"] = success_by_type
                
                # 更新时长统计（只统计成功的且不超过120秒的调用）
                if duration_seconds is not None and duration_seconds <= 120:
                    model_info["successful_calls"] = model_info.get("successful_calls", 0) + 1
                    model_info["total_duration_ms"] = model_info.get("total_duration_ms", 0) + int(duration_seconds * 1000)
                    
                    # 计算平均时长
                    successful_calls = model_info["successful_calls"]
                    if successful_calls > 0:
                        model_info["avg_duration_total"] = model_info["total_duration_ms"] / successful_calls / 1000
                    
                    # 更新今日时长（简化处理：使用滑动平均）
                    today_avg = model_info.get("avg_duration_today")
                    if today_avg is None:
                        model_info["avg_duration_today"] = duration_seconds
                    else:
                        model_info["avg_duration_today"] = (today_avg + duration_seconds) / 2
                
                # 更新最后成功时间
                model_info["last_success_at"] = _now_iso()
                
                # 更新配额信息（仅 ModelScope 有）
                if rate_headers and self._provider == "modelscope":
                    try:
                        if rate_headers.get("model_limit"):
                            model_info["daily_limit"] = int(rate_headers["model_limit"])
                    except (ValueError, TypeError):
                        pass
                    try:
                        if rate_headers.get("model_remaining"):
                            model_info["daily_remaining"] = int(rate_headers["model_remaining"])
                        else:
                            model_info["daily_remaining"] = max(0, model_info.get("daily_remaining", 0) - 1)
                    except (ValueError, TypeError):
                        model_info["daily_remaining"] = max(0, model_info.get("daily_remaining", 0) - 1)
                else:
                    # 火山引擎：简单递减
                    model_info["daily_remaining"] = max(0, model_info.get("daily_remaining", 0) - 1)
            else:
                # 更新错误计数
                if error_type:
                    error_counts = model_info.get("error_counts", {})
                    error_counts[error_type] = error_counts.get(error_type, 0) + 1
                    model_info["error_counts"] = error_counts
            
            self._save_config(self._config)
    
    def _has_available_quota(self) -> bool:
        """检查是否有任何模型还有配额（线程安全）"""
        with AIClient._config_lock:
            models_config = self._get_models_config()
            for model_info in models_config.values():
                if model_info.get("daily_remaining", 0) > 0:
                    return True
            return False
    
    def call(
        self,
        prompt: str,
        call_type: AICallType,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        max_attempts_per_model: int = 3,
        max_models: int = 3,
        validate_response: Optional[Callable[[str], bool]] = None
    ) -> Tuple[str, Dict[str, Any]]:
        """
        调用 AI 模型
        
        Args:
            prompt: 提示词
            call_type: 调用类型（TAG_JUDGMENT/SHORT_TEXT/LONG_THINKING）
            temperature: 温度参数（可选，默认使用配置文件中的值）
            max_tokens: 最大 token 数（可选，默认使用配置文件中的值）
            max_attempts_per_model: 每个模型最多尝试次数（仅火山引擎使用）
            max_models: 最多尝试多少个模型（仅火山引擎使用）
            validate_response: 响应验证函数，返回 True 表示验证通过
        
        Returns:
            Tuple[response_text, metadata]
            metadata 包含: model, call_type, duration_seconds, attempts, provider
        """
        # 获取默认参数
        default_params = self._get_default_params(call_type)
        if temperature is None:
            temperature = default_params.get("temperature", 0.4)
        if max_tokens is None:
            max_tokens = default_params.get("max_tokens", 128)
        
        # 获取模型顺序
        model_order = self._get_model_order(call_type)
        
        # 火山引擎、MiniMax 和中信 AI：使用单模型重试逻辑
        if self._provider in ["huoshan", "minimax", "zhongxin"]:
            return self._call_single_model_logic(
                prompt, call_type, temperature, max_tokens,
                max_attempts_per_model, validate_response
            )
        
        # ModelScope：使用新的优化逻辑
        return self._call_modelscope_logic(
            prompt, call_type, temperature, max_tokens,
            validate_response
        )
    
    def _call_single_model_logic(
        self,
        prompt: str,
        call_type: AICallType,
        temperature: float,
        max_tokens: int,
        max_attempts_per_model: int,
        validate_response: Optional[Callable[[str], bool]]
    ) -> Tuple[str, Dict[str, Any]]:
        """单模型调用逻辑（火山引擎、MiniMax 和中信 AI 使用）"""
        api_config = self._get_api_config()
        retry_delay = api_config.get("retry_delay_seconds", 10)
        max_retry_delay = api_config.get("max_retry_delay_seconds", 120)
        
        if self._provider == "huoshan":
            model = HUOSHAN_MODEL
        elif self._provider == "zhongxin":
            model = self._get_zhongxin_model()
        else:
            model = self._get_minimax_model()
        attempts = 0
        last_error = None
        
        while attempts < max_attempts_per_model:
            attempts += 1
            
            try:
                response, rate_headers, duration = self._call_api(
                    model, prompt, temperature, max_tokens
                )
                
                # 验证响应
                if validate_response and not validate_response(response):
                    last_error = AIModelError(f"返回内容未通过验证: {response[:120]!r}")
                    self._update_model_stats(model, call_type, False, error_type="validation_failed")
                    self._log(
                        f"[{model}] 第{attempts}次调用返回内容未通过验证: '{response[:50]}...'",
                        "WARN"
                    )
                    continue
                
                # 成功
                self._update_model_stats(model, call_type, True, duration, rate_headers)
                
                return response, {
                    "model": model,
                    "call_type": call_type.value,
                    "duration_seconds": duration,
                    "attempts": attempts,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "provider": self._provider,
                    "rate_headers": rate_headers,
                }
                
            except AIModelError as e:
                last_error = e
                error_msg = str(e)
                
                # 判断错误类型
                if "速率限制" in error_msg or "rate limit" in error_msg.lower():
                    error_type = "rate_limit"
                    self._update_model_stats(model, call_type, False, error_type=error_type)
                    if attempts < max_attempts_per_model:
                        wait_seconds = self._rate_limit_retry_delay(retry_delay, attempts, max_retry_delay)
                        self._log(f"[{model}] 速率限制，等待{wait_seconds}秒后重试...", "WARN")
                        time.sleep(wait_seconds)
                elif "超时" in error_msg or "timeout" in error_msg.lower():
                    error_type = "timeout"
                    self._update_model_stats(model, call_type, False, error_type=error_type)
                elif "内容为空" in error_msg:
                    error_type = "empty_response"
                    self._update_model_stats(model, call_type, False, error_type=error_type)
                else:
                    error_type = "other"
                    self._update_model_stats(model, call_type, False, error_type=error_type)
                
                self._log(f"[{model}] 第{attempts}次调用失败: {error_msg}", "WARN")
        
        raise AIAllModelsFailedError(
            f"{self._provider} 模型 {model} 连续 {attempts} 次失败。最后错误: {last_error}"
        )

    @staticmethod
    def _rate_limit_retry_delay(base_delay: int | float, attempt: int, max_delay: int | float = 120) -> float:
        """429 限流时指数退避，避免并发任务一起固定 10 秒后再次撞限流。"""
        try:
            base = float(base_delay)
        except (TypeError, ValueError):
            base = 10.0
        try:
            cap = float(max_delay)
        except (TypeError, ValueError):
            cap = 120.0
        delay = base * (2 ** max(attempt - 1, 0))
        return round(min(delay, cap), 1)
    
    def _call_modelscope_logic(
        self,
        prompt: str,
        call_type: AICallType,
        temperature: float,
        max_tokens: int,
        validate_response: Optional[Callable[[str], bool]]
    ) -> Tuple[str, Dict[str, Any]]:
        """
        ModelScope 调用逻辑（优化版）
        
        新特性：
        1. 429速率限制 → 立即换下一个模型
        2. 内容为空 → 等5秒后重试同一模型
        3. 所有模型限额耗尽 → 明确提示
        4. 取消"连续3个模型失败则放弃"，持续轮换
        5. 冷却器：连续3次429 + 300秒内 → 跳过该模型
        """
        model_order = self._get_model_order(call_type)
        last_error = None
        total_attempts = 0
        
        # 记录每个模型在本次调用中因内容为空重试的次数
        empty_retry_count = {}
        
        while total_attempts < MAX_TOTAL_ATTEMPTS:
            total_attempts += 1
            
            # 检查是否还有可用配额
            if not self._has_available_quota():
                raise AIQuotaExhaustedError(
                    "所有模型配额已耗尽，请等待配额重置或联系管理员"
                )
            
            # 遍历模型列表
            for model in model_order:
                # 检查配额
                models_config = self._get_models_config()
                model_info = models_config.get(model, {})
                remaining = model_info.get("daily_remaining", 0)
                if remaining <= 0:
                    continue
                
                # 检查冷却器
                if self._is_model_in_cooldown(model):
                    self._log(f"[{model}] 处于冷却期（连续{COOLDOWN_THRESHOLD}次429），跳过", "INFO")
                    continue
                
                try:
                    response, rate_headers, duration = self._call_api(
                        model, prompt, temperature, max_tokens
                    )
                    
                    # 验证响应
                    if validate_response and not validate_response(response):
                        last_error = AIModelError(f"返回内容未通过验证: {response[:120]!r}")
                        self._update_model_stats(model, call_type, False, error_type="validation_failed")
                        self._log(
                            f"[{model}] 返回内容未通过验证: '{response[:50]}...'",
                            "WARN"
                        )
                        # 验证失败，换下一个模型
                        continue
                    
                    # 成功
                    self._update_rate_limit_cooldown(model, False)  # 重置冷却计数
                    self._update_model_stats(model, call_type, True, duration, rate_headers)
                    
                    return response, {
                        "model": model,
                        "call_type": call_type.value,
                        "duration_seconds": duration,
                        "attempts": total_attempts,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        "provider": self._provider,
                        "rate_headers": rate_headers,
                    }
                    
                except AIModelError as e:
                    last_error = e
                    error_msg = str(e)
                    
                    # 判断错误类型并处理
                    if "速率限制" in error_msg or "rate limit" in error_msg.lower():
                        # 429速率限制：更新冷却计数，立即换下一个模型
                        self._update_rate_limit_cooldown(model, True)
                        self._update_model_stats(model, call_type, False, error_type="rate_limit")
                        self._log(f"[{model}] 速率限制，立即切换下一个模型", "WARN")
                        continue  # 换下一个模型
                    
                    elif "内容为空" in error_msg:
                        # 内容为空：等5秒后重试同一模型
                        self._update_rate_limit_cooldown(model, False)  # 重置冷却计数
                        self._update_model_stats(model, call_type, False, error_type="empty_response")
                        
                        retry_count = empty_retry_count.get(model, 0)
                        if retry_count < 2:  # 最多重试2次
                            empty_retry_count[model] = retry_count + 1
                            self._log(f"[{model}] 返回内容为空，等待5秒后重试...", "WARN")
                            time.sleep(5)
                            # 继续尝试同一模型（通过重新遍历实现）
                            break  # 跳出for循环，重新开始while循环
                        else:
                            self._log(f"[{model}] 连续返回空内容，切换下一个模型", "WARN")
                            continue  # 换下一个模型
                    
                    elif "超时" in error_msg or "timeout" in error_msg.lower():
                        # 超时：换下一个模型
                        self._update_rate_limit_cooldown(model, False)
                        self._update_model_stats(model, call_type, False, error_type="timeout")
                        self._log(f"[{model}] 请求超时，切换下一个模型", "WARN")
                        continue
                    
                    else:
                        # 其他错误：换下一个模型
                        self._update_rate_limit_cooldown(model, False)
                        self._update_model_stats(model, call_type, False, error_type="other")
                        self._log(f"[{model}] 调用失败: {error_msg[:100]}", "WARN")
                        continue
            
            # 如果for循环正常结束（没有break），说明所有模型都试过了
            # 继续下一轮while循环，重新开始遍历模型
            # 但如果所有模型都在冷却期或没有配额，会抛出异常
            if not self._has_available_quota():
                raise AIQuotaExhaustedError(
                    "所有模型配额已耗尽，请等待配额重置或联系管理员"
                )
        
        # 达到最大尝试次数
        raise AIAllModelsFailedError(
            f"已达到最大尝试次数({MAX_TOTAL_ATTEMPTS})，最后错误: {last_error}"
        )
    
    def call_for_tags(
        self,
        text: str,
        property_name: str,
        valid_options: List[str],
        prompt_template: str,
        max_attempts_per_model: int = 3,
        max_models: int = 3
    ) -> Tuple[Optional[List[str]], Dict[str, Any]]:
        """
        调用 AI 进行多标签判断
        
        Args:
            text: 需要判断的文本内容（AI要点部分）
            property_name: 属性名称（如"行业"或"公司"）
            valid_options: 有效选项列表
            prompt_template: prompt模板，使用 {options} 和 {text} 作为占位符
            max_attempts_per_model: 每个模型最多尝试次数（仅火山引擎使用）
            max_models: 最多尝试多少个模型（仅火山引擎使用）
        
        Returns:
            Tuple[tags_list, metadata]
            tags_list: 标签列表（可能为空列表表示无匹配）；如果所有模型都失败返回 None
            metadata: 包含调用信息
        """
        if not valid_options:
            self._log(f"属性 '{property_name}' 没有可选选项，跳过AI标签判断", "WARN")
            return [], {"error": "no_valid_options"}
        
        options_str = "、".join(valid_options)
        prompt = prompt_template.format(options=options_str, text=text)
        
        def extract_final_answer(response: str) -> str:
            """提取最终答案，去除思考过程（MiniMax 模型可能返回 <thinking> 标签）"""
            text = response.strip()
            
            # 处理 MiniMax 的 <thinking> 标签
            if '<thinking>' in text or '</thinking>' in text:
                # 尝试提取 </thinking> 之后的内容
                thinking_end = text.find('</thinking>')
                if thinking_end != -1:
                    text = text[thinking_end + len('</thinking>'):].strip()
                else:
                    # 如果只有 <thinking> 没有 </thinking>，尝试找换行后的内容
                    thinking_start = text.find('<thinking>')
                    if thinking_start != -1:
                        # 查找 <thinking> 后面的内容，跳过思考部分
                        remaining = text[thinking_start + len('<thinking>'):]
                        # 尝试找到第一个非空行
                        lines = remaining.split('\n')
                        for line in lines:
                            line = line.strip()
                            if line and not line.startswith('<') and not line.startswith('思考'):
                                text = line
                                break
            
            # 去除可能的 markdown 代码块标记
            if text.startswith('```'):
                lines = text.split('\n')
                text = '\n'.join(lines[1:-1] if lines[-1].strip() == '```' else lines[1:])
            
            return text.strip()
        
        def validate_tag_response(response: str) -> bool:
            """验证标签响应 - 允许多标签格式"""
            # 先提取最终答案
            cleaned = extract_final_answer(response)
            cleaned = cleaned.strip().strip('"\'「」【】')
            if not cleaned:
                return False
            if cleaned in ["无匹配", "无", "None", "none", "NULL", "null"]:
                return True
            tags = [t.strip() for t in re.split(r'[,，、\s]+', cleaned) if t.strip()]
            if not tags:
                return False
            for tag in tags:
                if tag in valid_options:
                    return True
                for opt in valid_options:
                    if tag in opt or opt in tag:
                        return True
            return False
        
        try:
            response, metadata = self.call(
                prompt=prompt,
                call_type=AICallType.TAG_JUDGMENT,
                temperature=0.1,
                max_tokens=256,
                max_attempts_per_model=max_attempts_per_model,
                max_models=max_models,
                validate_response=validate_tag_response
            )
            
            # 提取最终答案
            cleaned = extract_final_answer(response)
            cleaned = cleaned.strip().strip('"\'「」【】')
            
            if cleaned in ["无匹配", "无", "None", "none", "NULL", "null", "-"]:
                metadata["is_no_match"] = True
                return [], metadata
            
            raw_tags = [t.strip() for t in re.split(r'[,，、\s]+', cleaned) if t.strip()]
            
            matched_tags = []
            for tag in raw_tags:
                if tag in valid_options:
                    matched_tags.append(tag)
                    continue
                best_match = None
                for opt in valid_options:
                    if tag in opt or opt in tag:
                        if best_match is None or len(opt) > len(best_match):
                            best_match = opt
                if best_match and best_match not in matched_tags:
                    matched_tags.append(best_match)
            
            return matched_tags, metadata
            
        except (AIAllModelsFailedError, AIQuotaExhaustedError) as e:
            self._log(
                f"AI标签判断失败：{e}。属性: {property_name}, 有效选项: {valid_options[:10]}...",
                "ERROR"
            )
            return None, {"error": str(e)}
    
    def call_for_long_thinking(
        self,
        prompt: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        max_attempts_per_model: int = 3,
        max_models: int = 3
    ) -> Tuple[str, Dict[str, Any]]:
        """
        调用 AI 进行长篇思考
        
        Args:
            prompt: 提示词
            temperature: 温度参数（可选）
            max_tokens: 最大 token 数（可选）
            max_attempts_per_model: 每个模型最多尝试次数（仅火山引擎使用）
            max_models: 最多尝试多少个模型（仅火山引擎使用）
        
        Returns:
            Tuple[response, metadata]
        """
        return self.call(
            prompt=prompt,
            call_type=AICallType.LONG_THINKING,
            temperature=temperature,
            max_tokens=max_tokens,
            max_attempts_per_model=max_attempts_per_model,
            max_models=max_models
        )
    
    def get_model_stats(self, model: Optional[str] = None) -> Dict[str, Any]:
        """
        获取模型统计数据
        
        Args:
            model: 模型 ID，如果为 None 则返回所有模型的统计
        
        Returns:
            模型统计数据
        """
        models_config = self._get_models_config()
        if model:
            return models_config.get(model, {})
        return models_config
    
    def get_success_rate(self, model: str, call_type: Optional[AICallType] = None) -> float:
        """
        获取模型成功率
        
        Args:
            model: 模型 ID
            call_type: 调用类型，如果为 None 则返回总体成功率
        
        Returns:
            成功率（0.0 - 1.0）
        """
        models_config = self._get_models_config()
        model_info = models_config.get(model, {})
        
        if call_type:
            calls = model_info.get("calls_by_type", {}).get(call_type.value, 0)
            successes = model_info.get("success_by_type", {}).get(call_type.value, 0)
        else:
            calls = model_info.get("total_calls", 0)
            successes = model_info.get("successful_calls", 0)
        
        if calls == 0:
            return 0.0
        return successes / calls
    
    def get_provider(self) -> str:
        """获取当前使用的AI提供商"""
        return self._provider


def create_ai_client(log_func: Optional[Callable[[str, str], None]] = None, provider: Optional[str] = None) -> AIClient:
    """创建 AI 客户端实例
    
    Args:
        log_func: 日志函数
        provider: AI提供商，"modelscope"、"huoshan"、"minimax" 或 "zhongxin"，默认从配置文件读取
    """
    return AIClient(log_func=log_func, provider=provider)


def get_parallel_workers(provider: Optional[str] = None) -> int:
    """
    获取并行工作线程数（根据AI提供商不同而不同）
    
    Args:
        provider: AI提供商，如果为None则从配置文件读取
    
    Returns:
        并行工作线程数，modelscope默认5，huoshan默认20，minimax默认10，zhongxin默认5
    """
    try:
        if AI_MODELS_CONFIG_FILE.exists():
            with open(AI_MODELS_CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
            
            # 确定提供商
            if provider is None:
                provider = config.get("ai_provider", "modelscope")
            
            # 根据提供商返回不同的并行数
            if provider == "huoshan":
                return config.get("huoshan_parallel_workers", 20)
            elif provider == "minimax":
                return config.get("minimax_parallel_workers", 5)
            elif provider == "zhongxin":
                return config.get("zhongxin_parallel_workers", 5)
            else:
                return config.get("modelscope_parallel_workers", 5)
    except:
        pass
    
    # 默认值
    if provider == "huoshan":
        return 20
    elif provider == "minimax":
        return 5
    elif provider == "zhongxin":
        return 5
    return 5


def get_current_provider() -> str:
    """
    获取当前配置的AI提供商
    
    Returns:
        "modelscope"、"huoshan"、"minimax" 或 "zhongxin"
    """
    try:
        if AI_MODELS_CONFIG_FILE.exists():
            with open(AI_MODELS_CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
            return config.get("ai_provider", "modelscope")
    except:
        pass
    return "modelscope"

