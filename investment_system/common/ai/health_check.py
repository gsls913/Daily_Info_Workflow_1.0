from __future__ import annotations

import time
from typing import Any

from investment_system.common.ai.ai_client import AICallType, create_ai_client, get_current_provider


def _build_failure_hint(error: str, provider: str) -> str:
    lowered = error.lower()
    hints: list[str] = []

    if any(token in lowered for token in ["401", "unauthorized", "authorized_error", "invalid api key"]):
        hints.append("当前 AI 服务返回鉴权失败，请检查 data/credentials/AI_api_keys.txt 中对应提供商的 API Key 是否正确、未过期。")

    if any(token in lowered for token in ["quota", "余额", "insufficient", "payment", "billing", "credit", "429"]):
        hints.append("可能是额度不足、套餐到期或限流，请检查当前 AI 服务的 token plan、余额或调用频率。")

    if provider == "zhongxin":
        if any(token in lowered for token in ["getaddrinfo failed", "name resolution", "temporary failure in name resolution", "nodename nor servname"]):
            hints.append(
                "当前环境无法解析中信 AI 域名，请检查 DNS/代理设置，或在 data/config/ai_models.json 的 "
                "zhongxin_api_config.base_url 中填写当前网络可访问的中信 AI 地址。"
            )
        elif any(token in lowered for token in ["connection refused", "connection aborted", "connection reset", "failed to establish a new connection"]):
            hints.append("当前环境无法连接中信 AI 网关，请检查网络、代理或 zhongxin_api_config.base_url 是否可访问。")
        elif "http 503" in lowered or "service unavailable" in lowered:
            hints.append("中信 AI 网关返回 503，通常表示当前网络/代理未能连到后端服务，或网关暂时不可用；请对照可正常使用环境的 base_url 与代理设置。")
        elif not hints:
            hints.append("当前使用的是中信 AI，请检查中信 AI 的 base_url、模型名、网络代理和 API Key 配置。")

    return " ".join(hints)


def check_ai_health(log_func=None) -> dict[str, Any]:
    logs: list[str] = []

    def _log(message: str, level: str = "INFO") -> None:
        logs.append(f"[{level}] {message}")
        if log_func:
            log_func(message, level)

    started = time.perf_counter()
    try:
        client = create_ai_client(log_func=_log)
        response, metadata = client.call(
            prompt="ping",
            call_type=AICallType.SHORT_TEXT,
            temperature=0.1,
            max_tokens=64,
            max_attempts_per_model=2,
            max_models=1,
        )
        return {
            "ok": True,
            "provider": metadata.get("provider") or client.get_provider(),
            "model": metadata.get("model", ""),
            "duration_seconds": time.perf_counter() - started,
            "response": response[:100],
            "logs": logs[-10:],
        }
    except Exception as exc:
        error = str(exc)
        provider = get_current_provider()
        hint = _build_failure_hint(error, provider)
        return {
            "ok": False,
            "provider": provider,
            "duration_seconds": time.perf_counter() - started,
            "error": error,
            "hint": hint,
            "logs": logs[-10:],
        }

