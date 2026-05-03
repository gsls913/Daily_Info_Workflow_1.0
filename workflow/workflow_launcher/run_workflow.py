"""
Daily Info Workflow System - 统一工作流启动器
=====================================
功能：
1. 依次执行四个信息收集脚本
2. 提供统一的错误处理和日志记录
3. 显示进度和统计信息
4. 支持错误恢复和断点续传
5. 提供详细的错误诊断和建议

执行顺序：
1. AlphaPai会议纪要下载 (alphapai_download.py)
2. Notion微信文章收集 (notion_collector.py)
3. Alpha派微信公众号文章下载 (fetch_wechat_articles.py)
4. 小宇宙播客处理 (podcast_workflow.py)

使用方法：
  python run_workflow.py              # 执行完整工作流
  python run_workflow.py --step 1     # 只执行第1步
  python run_workflow.py --step 2     # 只执行第2步
  python run_workflow.py --step 3     # 只执行第3步
  python run_workflow.py --step 4     # 只执行第4步
  python run_workflow.py --resume     # 从上次失败处继续执行
  python run_workflow.py --status     # 查看上次执行状态
"""

import os
import sys
import time
import json
import argparse
import subprocess
import traceback
from datetime import datetime
from pathlib import Path

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    os.environ['PYTHONIOENCODING'] = 'utf-8'

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from common_libs.utils.notifications import show_windows_notification
from common_libs.ai.health_check import check_ai_health
from common_libs.config.config_loader import get as cfg
from common_libs.config.validator import validate_config
from common_libs.runtime.daily_digest import (
    archive_read_daily_digests,
    claim_first_full_run_today,
    claim_first_full_run_this_week,
    generate_daily_digest,
    generate_weekly_digest,
)
from common_libs.runtime.daily_report import write_workflow_report
from common_libs.runtime.maintenance import run_startup_maintenance
from common_libs.storage.download_history import save_json_atomic
from common_libs.utils.paths import (
    MEMO_HISTORY_FILE, WECHAT_HISTORY_FILE,
    PODCAST_HISTORY_FILE,
    WORKFLOW_PROGRESS_FILE, WORKFLOW_ERROR_LOG_FILE
)

PROGRESS_FILE = WORKFLOW_PROGRESS_FILE
ERROR_LOG_FILE = WORKFLOW_ERROR_LOG_FILE

WORKFLOW_STEPS = [
    {
        "name": "AlphaPai会议纪要下载",
        "script": os.path.join(PROJECT_ROOT, "alpha_memo_downloader", "alphapai_download.py"),
        "args": ["--auto"],
        "description": "下载AlphaPai会议纪要到Obsidian",
        "dependencies": ["playwright", "requests"],
        "common_errors": {
            "token": "AlphaPai登录token过期或无效",
            "network": "网络连接失败，无法访问AlphaPai服务器",
            "playwright": "Playwright浏览器未正确安装"
        }
    },
    {
        "name": "Notion微信文章收集",
        "script": os.path.join(PROJECT_ROOT, "notion_wechat_downloader", "notion_collector.py"),
        "args": [],
        "description": "从Notion数据库下载微信文章",
        "dependencies": ["notion-client", "requests", "beautifulsoup4"],
        "common_errors": {
            "notion_token": "Notion API token无效或过期",
            "database": "Notion数据库ID配置错误",
            "network": "网络连接失败，无法访问Notion API"
        }
    },
    {
        "name": "Alpha派微信公众号文章下载",
        "script": os.path.join(PROJECT_ROOT, "alpha_wechat_downloader", "fetch_wechat_articles.py"),
        "args": ["--auto"],
        "description": "下载订阅的微信公众号文章",
        "dependencies": ["playwright", "requests", "beautifulsoup4", "Pillow"],
        "common_errors": {
            "token": "AlphaPai登录token过期或无效",
            "network": "网络连接失败，无法访问AlphaPai服务器",
            "excel": "Excel配置文件不存在或格式错误"
        }
    },
    {
        "name": "小宇宙播客下载与AI要点整理",
        "script": os.path.join(PROJECT_ROOT, "podcast_process", "podcast_workflow.py"),
        "args": [],
        "description": "归档已读播客笔记，处理通义听悟已完成转录，并上传新小宇宙节目",
        "dependencies": ["requests", "playwright", "beautifulsoup4"],
        "common_errors": {
            "tingwu": "通义听悟登录状态失效或转录任务未完成",
            "xiaoyuzhou": "小宇宙页面解析失败或音频链接失效",
            "ai": "AI模型调用失败或API Key配置错误"
        }
    }
]


class ProgressManager:
    """进度管理器 - 负责保存和恢复执行进度"""

    @staticmethod
    def _ensure_progress_dir():
        os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
        os.makedirs(os.path.dirname(ERROR_LOG_FILE), exist_ok=True)

    @staticmethod
    def _write_progress(progress):
        ProgressManager._ensure_progress_dir()
        save_json_atomic(progress, PROGRESS_FILE)

    @staticmethod
    def save_step_started(step_idx, step_name):
        """记录步骤已开始，便于异常中断后恢复"""
        progress = ProgressManager.load_progress()
        progress["steps"][str(step_idx)] = {
            "name": step_name,
            "status": "running",
            "success": False,
            "error": None,
            "elapsed": 0.0,
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        progress["last_update"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        progress["completed_steps"] = [
            i for i, s in progress["steps"].items()
            if s.get("status") == "success" or s.get("success") is True
        ]
        progress["failed_steps"] = [
            i for i, s in progress["steps"].items()
            if s.get("status") in ("failed", "running") or (s.get("success") is False and s.get("error"))
        ]
        progress["running_steps"] = [
            i for i, s in progress["steps"].items()
            if s.get("status") == "running"
        ]

        try:
            ProgressManager._write_progress(progress)
        except Exception as e:
            print(f"⚠️  保存进度失败: {e}")
    
    @staticmethod
    def save_progress(step_idx, step_name, success, error_msg=None, elapsed=0.0):
        """保存执行进度"""
        progress = ProgressManager.load_progress()
        
        progress["steps"][str(step_idx)] = {
            "name": step_name,
            "status": "success" if success else "failed",
            "success": success,
            "error": error_msg,
            "elapsed": elapsed,
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        progress["last_update"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        progress["completed_steps"] = [
            i for i, s in progress["steps"].items()
            if s.get("status") == "success" or s.get("success") is True
        ]
        progress["failed_steps"] = [
            i for i, s in progress["steps"].items()
            if s.get("status") == "failed" or (s.get("success") is False and s.get("error"))
        ]
        progress["running_steps"] = [
            i for i, s in progress["steps"].items()
            if s.get("status") == "running"
        ]
        
        try:
            ProgressManager._write_progress(progress)
        except Exception as e:
            print(f"⚠️  保存进度失败: {e}")
    
    @staticmethod
    def load_progress():
        """加载执行进度"""
        if os.path.exists(PROGRESS_FILE):
            try:
                with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        
        return {
            "steps": {},
            "last_update": None,
            "completed_steps": [],
            "failed_steps": [],
            "running_steps": []
        }
    
    @staticmethod
    def get_last_failed_step():
        """获取需要恢复的最早失败/中断步骤"""
        progress = ProgressManager.load_progress()
        failed_steps = progress.get("failed_steps", [])
        running_steps = progress.get("running_steps", [])
        candidates = []
        for item in failed_steps + running_steps:
            try:
                candidates.append(int(item))
            except (TypeError, ValueError):
                pass
        
        if candidates:
            return min(candidates)
        return None
    
    @staticmethod
    def clear_progress():
        """清空进度记录"""
        ProgressManager._ensure_progress_dir()
        if os.path.exists(PROGRESS_FILE):
            try:
                os.remove(PROGRESS_FILE)
            except Exception:
                pass


class ErrorAnalyzer:
    """错误分析器 - 分析错误并提供诊断建议"""
    
    ERROR_PATTERNS = {
        "token_expired": {
            "patterns": ["token", "401", "unauthorized", "登录", "认证"],
            "diagnosis": "登录凭证过期或无效",
            "suggestions": [
                "检查配置文件中的登录凭证是否正确",
                "尝试手动运行脚本重新登录",
                "检查账号是否被锁定或限制"
            ]
        },
        "network_error": {
            "patterns": ["network", "connection", "timeout", "网络", "连接", "超时"],
            "diagnosis": "网络连接问题",
            "suggestions": [
                "检查网络连接是否正常",
                "检查防火墙或代理设置",
                "稍后重试（服务器可能暂时不可用）"
            ]
        },
        "playwright_error": {
            "patterns": ["playwright", "browser", "chromium", "浏览器"],
            "diagnosis": "Playwright浏览器问题",
            "suggestions": [
                "运行: playwright install chromium",
                "检查系统是否支持无头浏览器",
                "更新Playwright: pip install --upgrade playwright"
            ]
        },
        "module_not_found": {
            "patterns": ["modulenotfounderror", "no module named", "importerror"],
            "diagnosis": "Python依赖包缺失",
            "suggestions": [
                "检查requirements.txt是否完整安装",
                "运行: pip install -r requirements.txt",
                "检查Python环境是否正确"
            ]
        },
        "file_not_found": {
            "patterns": ["filenotfounderror", "no such file", "文件不存在"],
            "diagnosis": "文件或目录不存在",
            "suggestions": [
                "检查配置文件路径是否正确",
                "检查输出目录是否存在",
                "检查文件权限"
            ]
        },
        "excel_error": {
            "patterns": ["excel", "openpyxl", "xlsx"],
            "diagnosis": "Excel文件问题",
            "suggestions": [
                "检查Excel文件是否存在",
                "检查Excel文件格式是否正确",
                "安装openpyxl: pip install openpyxl"
            ]
        }
    }
    
    @staticmethod
    def analyze_error(error_msg, step_info):
        """分析错误并返回诊断结果"""
        if not error_msg:
            return None
        
        error_lower = error_msg.lower()
        
        for error_type, config in ErrorAnalyzer.ERROR_PATTERNS.items():
            for pattern in config["patterns"]:
                if pattern in error_lower:
                    return {
                        "type": error_type,
                        "diagnosis": config["diagnosis"],
                        "suggestions": config["suggestions"],
                        "original_error": error_msg[:500]
                    }
        
        return {
            "type": "unknown",
            "diagnosis": "未知错误",
            "suggestions": [
                "查看详细错误日志",
                "检查脚本是否正常运行",
                "联系开发者获取支持"
            ],
            "original_error": error_msg[:500]
        }
    
    @staticmethod
    def save_error_log(step_idx, step_name, error_msg, diagnosis=None):
        """保存错误日志"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        log_entry = f"""
{'='*70}
时间: {timestamp}
步骤: {step_idx} - {step_name}
{'='*70}
错误信息:
{error_msg}
"""
        
        if diagnosis:
            log_entry += f"""
诊断结果:
{diagnosis['diagnosis']}

建议解决方案:
"""
            for idx, suggestion in enumerate(diagnosis['suggestions'], 1):
                log_entry += f"  {idx}. {suggestion}\n"
        
        log_entry += "\n"
        
        try:
            os.makedirs(os.path.dirname(ERROR_LOG_FILE), exist_ok=True)
            with open(ERROR_LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(log_entry)
        except Exception:
            pass


def print_header(title):
    """打印标题头"""
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_step_header(step_num, total_steps, step_info):
    """打印步骤标题"""
    print("\n" + "=" * 70)
    print(f"📌 步骤 [{step_num}/{total_steps}]: {step_info['name']}")
    print(f"   说明: {step_info['description']}")
    print("=" * 70)


def print_diagnosis(diagnosis):
    """打印诊断结果"""
    print("\n" + "=" * 70)
    print("🔍 错误诊断")
    print("=" * 70)
    print(f"类型: {diagnosis['diagnosis']}")
    print(f"\n💡 建议解决方案:")
    for idx, suggestion in enumerate(diagnosis['suggestions'], 1):
        print(f"   {idx}. {suggestion}")
    print("=" * 70)


def get_user_power_choice() -> dict:
    """
    获取用户的电源操作选择
    
    Returns:
        dict: {
            'mode': int,  # 1-5
            'action': str,  # 'none', 'sleep', 'hibernate', 'shutdown', 'other_operations'
            'delay': int,  # 延迟秒数
            'description': str  # 描述文本
        }
    """
    print("\n" + "=" * 70)
    print("🌙 请选择运行模式")
    print("=" * 70)
    print("  1. 正常运行，结束后无额外操作")
    print("  2. 运行结束后5分钟睡眠（适合临时离开电脑）")
    print("  3. 运行结束后5分钟休眠（适合睡前、电脑有工作需继续）")
    print("  4. 运行结束后5分钟关机（适合睡前、无工作需继续）")
    print("  5. 其他操作")
    print("=" * 70)
    
    while True:
        try:
            choice = input("请输入选项 (1-5): ").strip()
            
            if choice == "1":
                return {
                    'mode': 1,
                    'action': 'none',
                    'delay': 0,
                    'description': '正常运行，结束后无额外操作'
                }
            elif choice == "2":
                return {
                    'mode': 2,
                    'action': 'sleep',
                    'delay': 300,  # 5分钟 = 300秒
                    'description': '运行结束后5分钟睡眠'
                }
            elif choice == "3":
                return {
                    'mode': 3,
                    'action': 'hibernate',
                    'delay': 300,
                    'description': '运行结束后5分钟休眠'
                }
            elif choice == "4":
                return {
                    'mode': 4,
                    'action': 'shutdown',
                    'delay': 300,
                    'description': '运行结束后5分钟关机'
                }
            elif choice == "5":
                return {
                    'mode': 5,
                    'action': 'other_operations',
                    'delay': 0,
                    'description': '其他操作'
                }
            else:
                print("❌ 无效选项，请输入 1-5")
        except KeyboardInterrupt:
            print("\n\n✅ 用户取消操作")
            sys.exit(0)


def get_power_choice_from_args(power_mode: str) -> dict:
    """将非交互式电源参数转换为内部选择结构"""
    choices = {
        "none": {
            'mode': 1,
            'action': 'none',
            'delay': 0,
            'description': '正常运行，结束后无额外操作'
        },
        "sleep": {
            'mode': 2,
            'action': 'sleep',
            'delay': 300,
            'description': '运行结束后5分钟睡眠'
        },
        "hibernate": {
            'mode': 3,
            'action': 'hibernate',
            'delay': 300,
            'description': '运行结束后5分钟休眠'
        },
        "shutdown": {
            'mode': 4,
            'action': 'shutdown',
            'delay': 300,
            'description': '运行结束后5分钟关机'
        },
    }
    return choices[power_mode]


def show_other_operations_menu():
    """显示其他操作菜单"""
    while True:
        print("\n" + "=" * 70)
        print("⚙️  其他操作")
        print("=" * 70)
        print("  1. 清除下载记录（可选具体清除规则）")
        print("  2. 返回上一层")
        print("=" * 70)
        
        try:
            choice = input("请输入选项 (1-2): ").strip()
            
            if choice == "1":
                clear_download_history()
                return  # 清除完成后返回主菜单
            elif choice == "2":
                return  # 返回上一层
            else:
                print("❌ 无效选项，请输入 1-2")
        except KeyboardInterrupt:
            print("\n\n✅ 返回上一层")
            return


def clear_download_history():
    """清除下载记录"""
    if not cfg("safety.allow_local_delete", True):
        print("\n安全开关 safety.allow_local_delete=false，禁止清除本地历史记录。")
        return
    print("\n" + "=" * 70)
    print("🗑️  清除下载记录")
    print("=" * 70)
    print("  请选择要清除的下载记录（可多选，用逗号分隔）：")
    print()
    print("  1. AlphaPai纪要的历史下载记录")
    print("     （清除后，再下载会默认下载每个类型下的前10篇）")
    print()
    print("  2. 微信公众号文章下载记录")
    print("     （清除后，再下载会默认下载每个公众号下的前3篇）")
    print()
    print("  3. 小宇宙播客下载/转录处理记录")
    print("     （清除后，再上传会默认抓取每个播客靠前的若干期，并重新处理已完成转录）")
    print()
    print("  4. 返回上一层")
    print()
    print("  示例：输入 \"1\" 只清除AlphaPai，输入 \"1,2\" 或 \"2,1\" 清除两者")
    print("         输入 \"4\" 返回上一层（单选）")
    print("=" * 70)
    
    while True:
        try:
            choice = input("请输入选项: ").strip()
            
            # 解析用户输入
            choices = [c.strip() for c in choice.split(',')]
            valid_choices = []
            
            for c in choices:
                if c in ['1', '2', '3', '4']:
                    if c not in valid_choices:
                        valid_choices.append(c)
            
            if not valid_choices:
                print("❌ 无效选项，请输入 1、2、3 或 4，或用逗号分隔多个选项")
                continue
            
            # 检查返回选项的特殊规则
            if '4' in valid_choices:
                if len(valid_choices) > 1:
                    print("❌ 无效选择：选项4（返回上一层）必须单选，不能与其他选项组合")
                    continue
                else:
                    # 单选4，返回上一层
                    return
            
            # 执行清除操作
            confirm_clear_operation(valid_choices)
            return  # 清除完成后返回主菜单
            
        except KeyboardInterrupt:
            print("\n\n✅ 返回上一层")
            return


def confirm_clear_operation(choices: list):
    """确认清除操作"""
    print("\n" + "=" * 70)
    print("⚠️  确认清除操作")
    print("=" * 70)
    
    clear_alpha = '1' in choices
    clear_wechat = '2' in choices
    clear_podcast = '3' in choices
    
    print("\n  即将执行以下操作：")
    
    if clear_alpha:
        alpha_file = MEMO_HISTORY_FILE
        print(f"\n  ✅ 清除 AlphaPai纪要的历史下载记录")
        print(f"     文件：{alpha_file}")
        print("     影响：清除后，再下载会默认下载每个类型下的前10篇")
    
    if clear_wechat:
        wechat_file = WECHAT_HISTORY_FILE
        print(f"\n  ✅ 清除 微信公众号文章下载记录")
        print(f"     文件：{wechat_file}")
        print("     影响：清除后，再下载会默认下载每个公众号下的前3篇")

    if clear_podcast:
        podcast_file = PODCAST_HISTORY_FILE
        print(f"\n  ✅ 清除 小宇宙播客下载/转录处理记录")
        print(f"     文件：{podcast_file}")
        print("     影响：清除后，再上传会默认抓取每个播客靠前的若干期，并重新处理已完成转录")
    
    print("\n" + "=" * 70)
    print("  按 Enter 键确认清除，按 Ctrl+C 取消...")
    print("=" * 70)
    
    try:
        input()
    except KeyboardInterrupt:
        print("\n\n✅ 已取消清除操作")
        return
    
    # 执行清除操作
    execute_clear_operation(clear_alpha, clear_wechat, clear_podcast)


def execute_clear_operation(clear_alpha: bool, clear_wechat: bool, clear_podcast: bool = False):
    """执行清除操作 - 保留JSON结构，只清空列表"""
    print("\n" + "=" * 70)
    print("🔄 正在清除下载记录...")
    print("=" * 70)
    
    success_count = 0
    
    if clear_alpha:
        alpha_file = MEMO_HISTORY_FILE
        print(f"  📁 目标文件: {alpha_file}")
        
        try:
            if not os.path.exists(alpha_file):
                print(f"  ❌ 文件不存在！")
            else:
                with open(alpha_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                print(f"  📊 读取到 {len(data)} 个分类")
                
                cleared_types = []
                total_cleared = 0
                for key in data:
                    if isinstance(data[key], list):
                        count = len(data[key])
                        if count > 0:
                            cleared_types.append(f"{key}({count}条)")
                            total_cleared += count
                        data[key] = []
                
                with open(alpha_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                
                print(f"  ✅ 已清除 AlphaPai纪要的历史下载记录")
                print(f"     共清除 {total_cleared} 条记录")
                if cleared_types:
                    print(f"     清除类型: {', '.join(cleared_types)}")
                success_count += 1
                
        except Exception as e:
            print(f"  ❌ 清除 AlphaPai纪要记录失败: {e}")
            import traceback
            traceback.print_exc()
    
    if clear_wechat:
        wechat_file = WECHAT_HISTORY_FILE
        print(f"  📁 目标文件: {wechat_file}")
        
        try:
            if not os.path.exists(wechat_file):
                print(f"  ❌ 文件不存在！")
            else:
                with open(wechat_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                print(f"  📊 读取到 {len(data)} 个公众号")
                
                cleared_accounts = []
                total_cleared = 0
                for key in data:
                    if isinstance(data[key], list):
                        count = len(data[key])
                        if count > 0:
                            cleared_accounts.append(f"{key}({count}条)")
                            total_cleared += count
                        data[key] = []
                
                with open(wechat_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                
                print(f"  ✅ 已清除 微信公众号文章下载记录")
                print(f"     共清除 {total_cleared} 条记录")
                if cleared_accounts:
                    print(f"     清除公众号: {', '.join(cleared_accounts)}")
                success_count += 1
                
        except Exception as e:
            print(f"  ❌ 清除 微信公众号记录失败: {e}")
            import traceback
            traceback.print_exc()

    if clear_podcast:
        podcast_file = PODCAST_HISTORY_FILE
        print(f"  📁 目标文件: {podcast_file}")

        try:
            if os.path.exists(podcast_file):
                with open(podcast_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            else:
                data = {}

            processed_count = len(data.get("processed_transcripts", [])) if isinstance(data.get("processed_transcripts"), list) else 0
            uploaded_count = sum(
                len(items) for items in data.get("uploaded_episodes", {}).values()
                if isinstance(items, list)
            ) if isinstance(data.get("uploaded_episodes"), dict) else 0
            uploads_count = len(data.get("uploads", {})) if isinstance(data.get("uploads"), dict) else 0

            data["processed_transcripts"] = []
            data["uploaded_episodes"] = {}
            data["uploads"] = {}
            if "processed_notes" in data:
                data["processed_notes"] = {}

            os.makedirs(os.path.dirname(podcast_file), exist_ok=True)
            temp_file = f"{podcast_file}.tmp"
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(temp_file, podcast_file)

            print(f"  ✅ 已清除 小宇宙播客下载/转录处理记录")
            print(f"     已清除: 已处理转录 {processed_count} 条，已上传节目 {uploaded_count} 条，上传映射 {uploads_count} 条")
            success_count += 1

        except Exception as e:
            print(f"  ❌ 清除 小宇宙播客记录失败: {e}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "=" * 70)
    if success_count > 0:
        print(f"✅ 清除完成！成功清除 {success_count} 个记录文件")
    else:
        print("❌ 清除失败，请检查文件路径")
    print("=" * 70)


def schedule_power_action(action: str, delay_seconds: int):
    """
    定时执行电源操作
    
    Args:
        action: 'sleep', 'hibernate', 'shutdown'
        delay_seconds: 延迟秒数
    """
    import subprocess
    
    action_names = {
        'sleep': '睡眠',
        'hibernate': '休眠',
        'shutdown': '关机'
    }
    
    action_name = action_names.get(action, '未知操作')
    
    if delay_seconds > 0:
        print("\n" + "=" * 70)
        print(f"⏰ 电脑将在 {delay_seconds//60} 分钟后{action_name}")
        print("   按 Ctrl+C 可取消")
        print("=" * 70)
        
        try:
            # 倒计时显示
            remaining = delay_seconds
            while remaining > 0:
                mins, secs = divmod(remaining, 60)
                print(f"\r   倒计时: {mins:02d}:{secs:02d} ", end="", flush=True)
                time.sleep(1)
                remaining -= 1
            
            print()  # 换行
        except KeyboardInterrupt:
            print(f"\n\n✅ 已取消定时{action_name}")
            return
    
    # 执行操作
    print(f"\n🌙 正在{action_name}...")
    
    try:
        if action == "sleep":
            # Windows睡眠命令（使用PowerShell，更可靠）
            subprocess.run([
                "powershell", "-Command",
                "Add-Type -AssemblyName System.Windows.Forms; " +
                "[System.Windows.Forms.Application]::SetSuspendState('Suspend', $false, $false)"
            ], check=True)
        elif action == "hibernate":
            # Windows休眠命令
            subprocess.run(["shutdown", "/h"], check=True)
        elif action == "shutdown":
            # Windows关机命令
            subprocess.run(["shutdown", "/s", "/t", "0"], check=True)
    except Exception as e:
        print(f"❌ {action_name}失败: {e}")


def confirm_and_start(choice: dict, step_indices: list, steps_to_run: list):
    """
    显示确认信息并等待用户确认
    
    Args:
        choice: 用户选择信息
        step_indices: 步骤索引列表
        steps_to_run: 要运行的步骤列表
    """
    print("\n" + "=" * 70)
    print(f"✅ 已选择：{choice['description']}")
    print("=" * 70)
    
    # 如果是关机选项，显示警告
    if choice['mode'] == 4:
        print("\n⚠️  重要提示：关机操作")
        print("=" * 70)
        print("  即将执行：工作流运行完成后5分钟关机")
        print()
        print("  请确保：")
        print("    ✅ 已保存所有工作文件")
        print("    ✅ 已关闭所有正在运行的程序")
        print("    ✅ 已备份重要数据")
        print()
        print("  关机前会有5分钟倒计时，期间可按Ctrl+C取消")
        print("=" * 70)
    
    # 显示将要执行的步骤
    print(f"\n📋 将执行以下步骤:")
    for i, step_info in zip(step_indices, steps_to_run):
        print(f"   {i}. {step_info['name']}")
    
    # 显示执行后的操作
    if choice['action'] != 'none':
        print(f"\n⏰ 执行完成后将：{choice['description']}")
        print("   （期间可按Ctrl+C取消）")
    
    print("\n" + "=" * 70)
    print("按 Enter 键开始执行，按 Ctrl+C 取消...")
    print("=" * 70)
    
    try:
        input()
    except KeyboardInterrupt:
        print("\n\n✅ 用户取消执行")
        sys.exit(0)


def print_execution_plan(choice: dict, step_indices: list, steps_to_run: list):
    """非交互模式下展示即将执行的计划"""
    print("\n" + "=" * 70)
    print(f"✅ 运行模式：{choice['description']}")
    print("=" * 70)
    print(f"\n📋 将执行以下步骤:")
    for i, step_info in zip(step_indices, steps_to_run):
        print(f"   {i}. {step_info['name']}")
    if choice['action'] != 'none':
        print(f"\n⏰ 执行完成后将：{choice['description']}")
    print("=" * 70)


def print_safety_notice(dry_run: bool):
    allow_local_delete = cfg("safety.allow_local_delete", True)
    allow_cloud_delete = cfg("safety.allow_cloud_delete", True)
    delete_tingwu = cfg("podcast.delete_tingwu_record_after_process", True)
    print("\n" + "=" * 70)
    print("安全开关")
    print("=" * 70)
    print(f"  dry-run: {'开启' if dry_run else '关闭'}")
    print(f"  本地删除: {'允许' if allow_local_delete else '禁止'}")
    print(f"  云端删除: {'允许' if allow_cloud_delete else '禁止'}")
    print(f"  听悟处理后删除云端记录: {'开启' if delete_tingwu else '关闭'}")
    print("=" * 70)


def run_script(script_path, args, step_name, step_idx):
    """
    执行单个脚本 (实时输出模式)
    
    Args:
        script_path: 脚本路径
        args: 脚本参数列表
        step_name: 步骤名称
        step_idx: 步骤索引
    
    Returns:
        tuple: (success: bool, elapsed_time: float, error_msg: str)
    """
    start_time = time.time()
    ProgressManager.save_step_started(step_idx, step_name)
    
    if not os.path.exists(script_path):
        error_msg = f"脚本不存在: {script_path}"
        print(f"❌ {error_msg}")
        ProgressManager.save_progress(step_idx, step_name, False, error_msg, 0.0)
        return False, 0.0, error_msg
    
    cmd = [sys.executable, script_path] + args
    
    print(f"\n🚀 开始执行: {step_name}")
    print(f"   命令: {' '.join(cmd)}")
    print(f"   开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("   提示: 正在执行中,请耐心等待...")
    print("-" * 70)
    sys.stdout.flush()  # 强制刷新输出缓冲区
    
    try:
        # 设置环境变量，禁用Python输出缓冲 + UTF-8编码
        env = os.environ.copy()
        env['PYTHONUNBUFFERED'] = '1'  # 禁用子进程的输出缓冲
        env['PYTHONIOENCODING'] = 'utf-8'  # 子进程使用UTF-8编码
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # 合并stderr到stdout，统一处理
            text=True,
            encoding='utf-8',
            errors='replace',  # 替换无法解码的字符
            bufsize=1,  # 行缓冲
            env=env,  # 传递环境变量
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0  # Windows: 不创建新窗口
        )
        
        stdout_lines = []
        
        import threading
        
        def read_stream(stream, lines_list):
            """实时读取输出流（健壮版本）"""
            try:
                while True:
                    try:
                        line = stream.readline()
                        if not line:  # EOF
                            break
                        
                        line = line.rstrip()
                        if not line:  # 跳过空行
                            continue
                        
                        # 安全打印（处理所有编码错误）
                        try:
                            print(f"   {line}", flush=True)
                        except (UnicodeEncodeError, UnicodeDecodeError):
                            safe_line = line.encode('utf-8', errors='replace').decode('utf-8')
                            print(f"   {safe_line}", flush=True)
                        
                        lines_list.append(line)
                    
                    except UnicodeError:
                        continue
                    
                    except Exception as e:
                        break
                
                stream.close()
                
            except Exception as e:
                pass
        
        stdout_thread = threading.Thread(target=read_stream, args=(process.stdout, stdout_lines))
        stdout_thread.start()
        
        try:
            process.wait(timeout=7200)
            
            stdout_thread.join(timeout=5)  # 等待输出线程结束
            
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            elapsed = time.time() - start_time
            error_msg = f"脚本执行超时（超过7200秒）"
            print("-" * 70)
            print(f"❌ {error_msg}")
            ProgressManager.save_progress(step_idx, step_name, False, error_msg, elapsed)
            return False, elapsed, error_msg
        
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except:
                    process.kill()
        
        elapsed = time.time() - start_time
        output = '\n'.join(stdout_lines) if stdout_lines else ''
        
        if process.returncode == 0:
            print("-" * 70)
            print(f"✅ {step_name} 执行成功")
            print(f"   用时: {elapsed:.1f} 秒")
            ProgressManager.save_progress(step_idx, step_name, True, None, elapsed)
            return True, elapsed, None
        else:
            error_msg = output or "未知错误"
            
            print("-" * 70)
            print(f"❌ {step_name} 执行失败 (退出码: {process.returncode})")
            print(f"   用时: {elapsed:.1f} 秒")
            
            if error_msg and len(error_msg) > 0:
                print(f"\n⚠️  错误输出:")
                print("-" * 70)
                print(error_msg[:1500])
                print("-" * 70)
            
            ProgressManager.save_progress(step_idx, step_name, False, error_msg, elapsed)
            
            return False, elapsed, error_msg
            
    except Exception as e:
        elapsed = time.time() - start_time
        error_msg = f"{type(e).__name__}: {str(e)}"
        print("-" * 70)
        print(f"❌ {step_name} 执行异常: {error_msg}")
        print(f"   用时: {elapsed:.1f} 秒")
        ProgressManager.save_progress(step_idx, step_name, False, error_msg, elapsed)
        return False, elapsed, error_msg


def show_status():
    """显示上次执行状态"""
    progress = ProgressManager.load_progress()
    
    print_header("工作流执行状态")
    
    if not progress.get("steps"):
        print("\n📭 没有执行记录")
        return
    
    print(f"\n最后更新: {progress.get('last_update', '未知')}")
    print(f"已完成步骤: {len(progress.get('completed_steps', []))}")
    print(f"失败步骤: {len(progress.get('failed_steps', []))}")
    
    print(f"\n📋 详细状态:")
    for step_idx, step_data in sorted(progress["steps"].items(), key=lambda x: int(x[0])):
        step_status = step_data.get("status")
        if step_status == "running":
            status = "⏸️"
        elif step_status == "success" or step_data.get("success"):
            status = "✅"
        else:
            status = "❌"
        print(f"   {status} 步骤{step_idx}: {step_data['name']}")
        if step_status:
            print(f"      状态: {step_status}")
        print(f"      时间: {step_data.get('timestamp', '未知')}")
        print(f"      用时: {step_data.get('elapsed', 0):.1f}秒")
        if step_data.get("error"):
            print(f"      错误: {step_data['error'][:100]}...")
    
    print("\n" + "=" * 70)
    
    if progress.get("failed_steps") or progress.get("running_steps"):
        print(f"\n💡 提示: 使用 --resume 从失败处继续执行")
        next_step = ProgressManager.get_last_failed_step()
        if next_step:
            print(f"   或使用 --step {next_step} 重试特定步骤")


def check_dependencies():
    """检查依赖包"""
    print("\n🔍 检查依赖包...")
    
    required_packages = {
        "requests": "requests",
        "beautifulsoup4": "bs4",
        "playwright": "playwright",
        "Pillow": "PIL",
        "openpyxl": "openpyxl",
        "notion-client": "notion_client"
    }
    
    missing = []
    for package_name, import_name in required_packages.items():
        try:
            __import__(import_name)
            print(f"   ✅ {package_name}")
        except ImportError:
            print(f"   ❌ {package_name} (缺失)")
            missing.append(package_name)
    
    if missing:
        print(f"\n⚠️  缺少依赖包: {', '.join(missing)}")
        print(f"   请运行: pip install {' '.join(missing)}")
        return False
    
    print("\n✅ 所有依赖包已安装")
    return True


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='Daily Info Workflow System - 统一工作流启动器',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python run_workflow.py              # 执行完整工作流
  python run_workflow.py --step 1     # 只执行第1步 (AlphaPai会议纪要)
  python run_workflow.py --step 2     # 只执行第2步 (Notion微信文章)
  python run_workflow.py --step 3     # 只执行第3步 (微信公众号文章)
  python run_workflow.py --step 4     # 只执行第4步 (小宇宙播客)
  python run_workflow.py --resume     # 从上次失败处继续执行
  python run_workflow.py --status     # 查看上次执行状态
  python run_workflow.py --clear      # 清空执行记录
  python run_workflow.py --yes --power none  # 非交互式运行，适合计划任务
        """
    )
    
    parser.add_argument(
        '--step',
        type=int,
        choices=[1, 2, 3, 4],
        default=None,
        help='只执行指定步骤 (1-4)'
    )
    
    parser.add_argument(
        '--resume',
        action='store_true',
        help='从上次失败处继续执行'
    )
    
    parser.add_argument(
        '--status',
        action='store_true',
        help='查看上次执行状态'
    )
    
    parser.add_argument(
        '--clear',
        action='store_true',
        help='清空执行记录'
    )
    
    parser.add_argument(
        '--skip-check',
        action='store_true',
        help='跳过依赖检查'
    )

    parser.add_argument(
        '--yes',
        action='store_true',
        help='非交互式确认执行（保留原人工流程；仅指定本参数时跳过菜单和确认）'
    )

    parser.add_argument(
        '--power',
        choices=['none', 'sleep', 'hibernate', 'shutdown'],
        default='none',
        help='非交互式结束动作，配合 --yes 使用（默认 none）'
    )

    parser.add_argument(
        '--continue-on-error',
        action='store_true',
        help='某一步失败后继续执行后续步骤（非交互式推荐）'
    )

    parser.add_argument(
        '--fail-fast',
        action='store_true',
        help='某一步失败后立即停止'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='只展示将执行的步骤和命令，不真正运行子工作流'
    )

    parser.add_argument(
        '--skip-ai-health-check',
        action='store_true',
        help='跳过运行前 AI 连通性检查'
    )
    
    args = parser.parse_args()
    
    if args.status:
        show_status()
        return
    
    if args.clear:
        ProgressManager.clear_progress()
        if os.path.exists(ERROR_LOG_FILE):
            os.remove(ERROR_LOG_FILE)
        print("✅ 执行记录已清空")
        return
    
    if not args.skip_check:
        if not check_dependencies():
            print("\n❌ 依赖检查失败，请先安装缺失的依赖包")
            print("   或使用 --skip-check 跳过检查")
            return

    config_ok, config_errors, config_warnings = validate_config()
    if config_warnings:
        print("\n⚠️  配置提醒:")
        for item in config_warnings:
            print(f"   - {item}")
    if not config_ok:
        print("\n❌ 配置校验失败:")
        for item in config_errors:
            print(f"   - {item}")
        show_windows_notification("日常信息工作流未启动", "配置校验失败，请检查 config.yaml 和凭证文件")
        return

    if not args.dry_run:
        run_startup_maintenance()
        archived_digests = archive_read_daily_digests()
        if archived_digests:
            print(f"\n📦 已归档日报/周报: {archived_digests} 篇")

    ai_health = None
    if not args.dry_run and not args.skip_ai_health_check:
        print("\n🤖 运行前 AI 连通性检查...")
        ai_health = check_ai_health(lambda msg, level="INFO": print(f"   [{level}] {msg}"))
        if not ai_health.get("ok"):
            print("\n❌ AI 模型预检失败，后续工作流已终止")
            print(f"   错误: {ai_health.get('error')}")
            if ai_health.get("hint"):
                print(f"   提醒: {ai_health.get('hint')}")
            show_windows_notification(
                "AI 模型预检失败",
                ai_health.get("hint") or "请检查 AI API Key、模型额度或续费状态"
            )
            write_workflow_report(
                results=[],
                started_at=datetime.now(),
                elapsed_seconds=0,
                dry_run=False,
                config_warnings=config_warnings,
                ai_health=ai_health,
                notes=["AI 预检失败，工作流未启动。"],
            )
            return

    should_generate_daily_digest = (
        not args.dry_run
        and not args.step
        and not args.resume
        and claim_first_full_run_today()
    )
    should_generate_weekly_digest = (
        not args.dry_run
        and not args.step
        and not args.resume
        and claim_first_full_run_this_week()
    )
    
    workflow_start = time.time()
    
    print_header("Daily Info Workflow System - 统一工作流启动器")
    print(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Python版本: {sys.version.split()[0]}")
    print(f"工作目录: {PROJECT_ROOT}")
    
    if args.yes:
        power_choice = get_power_choice_from_args(args.power)
        print(f"\n🤖 非交互式模式: {power_choice['description']}")
    else:
        # 主菜单循环
        while True:
            # 获取用户的电源操作选择
            power_choice = get_user_power_choice()
            
            # 如果选择了"其他操作"
            if power_choice['mode'] == 5:
                show_other_operations_menu()
                continue  # 继续循环，重新显示主菜单
            
            # 如果选择了运行模式（1-4），退出循环，开始执行工作流
            break
    
    if args.resume:
        last_failed = ProgressManager.get_last_failed_step()
        if last_failed:
            print(f"\n🔄 恢复模式: 从步骤 {last_failed} 继续执行")
            start_step = last_failed
        else:
            print(f"\n🔄 恢复模式: 没有失败的步骤，将执行完整工作流")
            start_step = 1
        step_indices = list(range(start_step, len(WORKFLOW_STEPS) + 1))
        steps_to_run = [WORKFLOW_STEPS[i-1] for i in step_indices]
    elif args.step:
        print(f"\n🎯 单步模式: 只执行步骤 {args.step}")
        steps_to_run = [WORKFLOW_STEPS[args.step - 1]]
        step_indices = [args.step]
    else:
        print(f"\n🔄 完整模式: 执行所有 {len(WORKFLOW_STEPS)} 个步骤")
        steps_to_run = WORKFLOW_STEPS
        step_indices = list(range(1, len(WORKFLOW_STEPS) + 1))
    
    if args.yes:
        print_execution_plan(power_choice, step_indices, steps_to_run)
    else:
        # 显示确认信息并等待用户确认
        confirm_and_start(power_choice, step_indices, steps_to_run)

    print_safety_notice(args.dry_run)
    if args.dry_run:
        print("\n🧪 dry-run 模式：以下命令不会实际执行")
        for step_idx, step_info in zip(step_indices, steps_to_run):
            cmd = [sys.executable, step_info['script']] + step_info['args']
            print(f"   步骤 {step_idx}: {' '.join(cmd)}")
        report_path = write_workflow_report(
            results=[
                {
                    "step": step_idx,
                    "name": step_info["name"],
                    "success": True,
                    "elapsed": 0.0,
                    "error": None,
                }
                for step_idx, step_info in zip(step_indices, steps_to_run)
            ],
            started_at=datetime.now(),
            elapsed_seconds=0.0,
            dry_run=True,
            config_warnings=config_warnings,
            ai_health={"ok": True, "provider": "skipped", "model": "dry-run"},
            notes=["dry-run 仅预演步骤，没有执行任何下载、上传、写入或删除。"],
        )
        print(f"\n📄 dry-run 摘要已生成: {report_path}")
        return
    
    results = []
    total_elapsed = 0.0
    should_stop = False
    
    for idx, (step_idx, step_info) in enumerate(zip(step_indices, steps_to_run)):
        print_step_header(step_idx, len(WORKFLOW_STEPS), step_info)
        
        success, elapsed, error_msg = run_script(
            step_info['script'],
            step_info['args'],
            step_info['name'],
            step_idx
        )
        
        results.append({
            'step': step_idx,
            'name': step_info['name'],
            'success': success,
            'elapsed': elapsed,
            'error': error_msg
        })
        
        total_elapsed += elapsed
        
        if not success:
            diagnosis = ErrorAnalyzer.analyze_error(error_msg, step_info)
            
            if diagnosis:
                print_diagnosis(diagnosis)
                ErrorAnalyzer.save_error_log(step_idx, step_info['name'], error_msg, diagnosis)
            else:
                ErrorAnalyzer.save_error_log(step_idx, step_info['name'], error_msg)
            
            if args.fail_fast:
                print(f"\n⏹️  步骤 {step_idx} 失败，已按 --fail-fast 停止")
                should_stop = True
                break

            if args.yes:
                if args.continue_on_error:
                    print(f"\n⚠️  步骤 {step_idx} 失败，按 --continue-on-error 继续执行下一步")
                else:
                    print(f"\n⏹️  步骤 {step_idx} 失败，非交互模式默认停止")
                    should_stop = True
                    break
            elif not args.step:
                print(f"\n⚠️  步骤 {step_idx} 失败")
                print("   按任意键继续执行下一步，或按 Ctrl+C 退出...")
                try:
                    input()
                except KeyboardInterrupt:
                    print("\n\n⏹️  用户中断执行")
                    should_stop = True
                    break
        
        time.sleep(1)
    
    workflow_elapsed = time.time() - workflow_start
    
    print_header("工作流执行总结")
    
    success_count = sum(1 for r in results if r['success'])
    failed_count = len(results) - success_count
    
    print(f"\n📊 执行统计:")
    print(f"   总步骤数: {len(results)}")
    print(f"   成功: {success_count}")
    print(f"   失败: {failed_count}")
    print(f"   总用时: {workflow_elapsed:.1f} 秒")
    print(f"   脚本执行用时: {total_elapsed:.1f} 秒")
    
    print(f"\n📋 详细结果:")
    for r in results:
        status = "✅" if r['success'] else "❌"
        print(f"   {status} 步骤{r['step']}: {r['name']} ({r['elapsed']:.1f}s)")
    
    print("\n" + "=" * 70)
    
    if success_count == len(results):
        print("🎉 所有步骤执行成功！")
        ProgressManager.clear_progress()
        show_windows_notification(
            "日常信息工作流完成",
            f"成功执行 {success_count} 个步骤，用时 {workflow_elapsed:.0f} 秒"
        )
    elif success_count > 0:
        print(f"⚠️  部分步骤执行成功 ({success_count}/{len(results)})")
        print(f"\n💡 提示:")
        print(f"   - 使用 --resume 从失败处继续执行")
        print(f"   - 使用 --status 查看详细状态")
        print(f"   - 查看错误日志: {ERROR_LOG_FILE}")
        show_windows_notification(
            "日常信息工作流部分完成",
            f"成功 {success_count} 个，失败 {failed_count} 个"
        )
    else:
        print("❌ 所有步骤执行失败")
        print(f"\n💡 提示:")
        print(f"   - 查看错误日志: {ERROR_LOG_FILE}")
        print(f"   - 使用 --status 查看详细状态")
        print(f"   - 检查依赖包: python run_workflow.py --skip-check")
        show_windows_notification(
            "日常信息工作流失败",
            f"所有 {len(results)} 个步骤执行失败"
        )
    
    print("=" * 70 + "\n")

    report_path = write_workflow_report(
        results=results,
        started_at=datetime.fromtimestamp(workflow_start),
        elapsed_seconds=workflow_elapsed,
        dry_run=False,
        config_warnings=config_warnings,
        ai_health=ai_health,
        notes=[
            "如某个来源积累了过多未处理内容，各子工作流会按 config.yaml 中的每源上限处理，并在日志中说明达到上限。"
        ],
    )
    print(f"📄 每日运行摘要已生成: {report_path}")

    if should_generate_daily_digest:
        print("\n🗞️  今天第一次完整运行，开始生成信息汇总日报...")
        digest_path = generate_daily_digest()
        if digest_path:
            print(f"🗞️  信息汇总日报已生成: {digest_path}")
        else:
            print("⚠️  信息汇总日报生成失败，详情请查看运行日志。")

    if should_generate_weekly_digest:
        print("\n🗞️  本周第一次完整运行，开始生成信息汇总周报...")
        weekly_path = generate_weekly_digest()
        if weekly_path:
            print(f"🗞️  信息汇总周报已生成: {weekly_path}")
        else:
            print("⚠️  信息汇总周报生成失败，详情请查看运行日志。")
    
    # 执行电源操作
    if power_choice['action'] != 'none':
        schedule_power_action(power_choice['action'], power_choice['delay'])


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⏹️  用户中断执行")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ 工作流执行异常: {e}")
        print(f"\n堆栈跟踪:")
        traceback.print_exc()
        sys.exit(1)
