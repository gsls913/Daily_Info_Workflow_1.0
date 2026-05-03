from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

from common_libs.config.config_loader import get, get_podcast_accounts
from common_libs.utils.paths import PROJECT_ROOT


def _positive_int(key: str, default: int, warnings: list[str]) -> None:
    value = get(key, default)
    if not isinstance(value, int) or value < 0:
        warnings.append(f"{key} 应为非负整数，当前值: {value!r}")


def validate_config() -> tuple[bool, list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    obsidian_base = get("paths.obsidian_base_dir")
    if not obsidian_base:
        errors.append("paths.obsidian_base_dir 未配置")
    elif not Path(obsidian_base).exists():
        warnings.append(f"Obsidian 根目录不存在或当前不可访问: {obsidian_base}")

    for key, default in [
        ("wechat.new_account_download_count", 3),
        ("wechat.max_download_per_account", 10),
        ("podcast.new_account_download_count", 3),
        ("podcast.max_download_per_account", 5),
        ("memo.new_source_download_count", 10),
        ("memo.max_download_per_source", 20),
        ("notion.page_size", 100),
    ]:
        _positive_int(key, default, warnings)

    for idx, account in enumerate(get_podcast_accounts(), 1):
        url = account.get("url") if isinstance(account, dict) else str(account)
        parsed = urlparse(str(url))
        if not parsed.scheme or "xiaoyuzhoufm.com" not in parsed.netloc:
            warnings.append(f"podcast.accounts 第 {idx} 项 URL 看起来不是小宇宙播客主页: {url}")

    ai_models = Path(PROJECT_ROOT) / "data" / "config" / "ai_models.json"
    legacy_ai_models = Path(PROJECT_ROOT) / "workflow" / "ai" / "ai_models.json"
    ai_keys = Path(PROJECT_ROOT) / "data" / "credentials" / "AI_api_keys.txt"
    if not ai_models.exists():
        errors.append(f"AI 模型配置不存在: {ai_models}")
        if legacy_ai_models.exists():
            warnings.append(f"检测到旧位置 AI 模型配置，可复制到新位置: {legacy_ai_models}")
    if not ai_keys.exists() and not any(os.environ.get(f"{name}_API_KEY") for name in ["MINIMAX", "HUOSHAN", "MODELSCOPE"]):
        errors.append("未找到 AI_api_keys.txt，且未检测到 MINIMAX/HUOSHAN/MODELSCOPE_API_KEY 环境变量")

    return not errors, errors, warnings
