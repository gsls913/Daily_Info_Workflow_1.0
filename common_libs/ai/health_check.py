from __future__ import annotations

import time
from typing import Any

from common_libs.ai.ai_client import AICallType, create_ai_client


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
        hint = ""
        lowered = error.lower()
        if any(token in lowered for token in ["401", "unauthorized", "authorized_error", "invalid api key"]):
            hint = "当前 AI 服务返回鉴权失败，请检查 data/credentials/AI_api_keys.txt 中对应提供商的 API Key 是否正确、未过期。"
        if any(token in lowered for token in ["quota", "余额", "insufficient", "payment", "billing", "credit", "429"]):
            hint = "可能是额度不足、套餐到期或限流，请检查 MiniMax/当前 AI 服务的 token plan、余额或续费状态。"
        return {
            "ok": False,
            "duration_seconds": time.perf_counter() - started,
            "error": error,
            "hint": hint,
            "logs": logs[-10:],
        }
