from __future__ import annotations

import re


BAD_RESPONSE_PATTERNS = [
    "无法处理",
    "无法回答",
    "没有提供",
    "未提供文本",
    "作为一个AI",
    "as an ai",
]


def clean_thinking_tags(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL | re.IGNORECASE)
    return text.strip()


def basic_ai_response_ok(text: str, min_chars: int = 20) -> tuple[bool, str]:
    cleaned = clean_thinking_tags(text)
    if len(cleaned) < min_chars:
        return False, f"AI 响应过短（{len(cleaned)} 字符）"
    lowered = cleaned.lower()
    for pattern in BAD_RESPONSE_PATTERNS:
        if pattern.lower() in lowered:
            return False, f"AI 响应疑似失败话术: {pattern}"
    return True, ""
