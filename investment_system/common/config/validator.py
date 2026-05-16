from __future__ import annotations

import os
import json
from pathlib import Path
from urllib.parse import urlparse

from investment_system.common.config.config_loader import get, get_config_load_error
from investment_system.common.config.source_config import SourceConfigError, load_podcast_accounts
from investment_system.common.utils.paths import PROJECT_ROOT


def _positive_int(key: str, default: int, warnings: list[str]) -> None:
    value = get(key, default)
    if not isinstance(value, int) or value < 0:
        warnings.append(f"{key} 应为非负整数，当前值: {value!r}")


def validate_config() -> tuple[bool, list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    config_error = get_config_load_error()
    if config_error:
        errors.append(config_error)
        return False, errors, warnings

    obsidian_base = get("paths.obsidian_base_dir")
    if not obsidian_base:
        errors.append("paths.obsidian_base_dir 未配置")
    elif not Path(obsidian_base).exists():
        warnings.append(f"Obsidian 根目录不存在或当前不可访问: {obsidian_base}")

    investment_notes_base = get("paths.investment_notes_base_dir")
    if investment_notes_base and not Path(str(investment_notes_base)).exists():
        warnings.append(f"投资笔记根目录不存在或当前不可访问: {investment_notes_base}")

    obsidian_exe = get("paths.obsidian_exe_path")
    if obsidian_exe and not Path(str(obsidian_exe)).exists():
        warnings.append(f"Obsidian 程序路径不存在或当前不可访问: {obsidian_exe}")

    shortcut_staging_dir = get("paths.obsidian_shortcut_staging_dir")
    if shortcut_staging_dir:
        shortcut_parent = Path(str(shortcut_staging_dir)).parent
        if shortcut_parent and not shortcut_parent.exists():
            warnings.append(f"Obsidian 快捷方式待添加目录的父目录不存在，首次创建时会尝试创建: {shortcut_parent}")

    for key, default in [
        ("wechat.new_account_download_count", 3),
        ("wechat.max_download_per_account", 10),
        ("podcast.new_account_download_count", 3),
        ("podcast.max_download_per_account", 5),
        ("podcast.max_completed_transcripts_per_run", 5),
        ("memo.new_source_download_count", 10),
        ("memo.max_download_per_source", 20),
        ("notion.page_size", 100),
    ]:
        _positive_int(key, default, warnings)

    try:
        podcast_accounts = load_podcast_accounts()
    except SourceConfigError as exc:
        warnings.append(f"小宇宙账号配置读取失败: {exc}")
        podcast_accounts = []
    for idx, account in enumerate(podcast_accounts, 1):
        url = account.get("url") if isinstance(account, dict) else str(account)
        parsed = urlparse(str(url))
        if not parsed.scheme or "xiaoyuzhoufm.com" not in parsed.netloc:
            warnings.append(f"podcast_account 第 {idx} 项 URL 看起来不是小宇宙播客主页: {url}")

    ai_models = Path(PROJECT_ROOT) / "data" / "config" / "ai_models.json"
    legacy_ai_models = Path(PROJECT_ROOT) / "workflow" / "ai" / "ai_models.json"
    ai_keys = Path(PROJECT_ROOT) / "data" / "credentials" / "AI_api_keys.txt"
    if not ai_models.exists():
        errors.append(f"AI 模型配置不存在: {ai_models}")
        if legacy_ai_models.exists():
            warnings.append(f"检测到旧位置 AI 模型配置，可复制到新位置: {legacy_ai_models}")
    else:
        try:
            ai_config = json.loads(ai_models.read_text(encoding="utf-8"))
            runtime_provider = ai_config.get("ai_provider")
            yaml_provider = get("ai.default_provider")
            if yaml_provider and runtime_provider and yaml_provider != runtime_provider:
                warnings.append(
                    "AI provider 当前以 data/config/ai_models.json 为准；"
                    f"config.yaml 中 ai.default_provider={yaml_provider!r}，"
                    f"实际生效 ai_provider={runtime_provider!r}"
                )
            if runtime_provider == "zhongxin":
                model = ai_config.get("zhongxin_default_model")
                if not model:
                    errors.append("中信 AI 已启用，但 zhongxin_default_model 未配置")
        except Exception as exc:
            errors.append(f"AI 模型配置读取失败: {ai_models} ({exc})")

    if not ai_keys.exists() and not (
        any(os.environ.get(f"{name}_API_KEY") for name in ["MINIMAX", "HUOSHAN", "MODELSCOPE", "ZHONGXIN"])
        or os.environ.get("ANTHROPIC_API_KEY")
    ):
        errors.append("未找到 AI_api_keys.txt，且未检测到 MINIMAX/HUOSHAN/MODELSCOPE/ZHONGXIN_API_KEY 或 ANTHROPIC_API_KEY 环境变量")

    recycle_dir = get("safety.recycle_bin_dir")
    if recycle_dir:
        recycle_parent = Path(str(recycle_dir)).expanduser().parent
        if recycle_parent and not recycle_parent.exists():
            warnings.append(f"本地回收站父目录不存在，首次删除时会尝试创建: {recycle_parent}")
    _positive_int("safety.recycle_bin_retention_days", 10, warnings)

    return not errors, errors, warnings

