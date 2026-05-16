from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from investment_system.common.markdown_utils import normalize_markdown_output
from investment_system.common.utils.paths import PROJECT_ROOT


REPORT_DIR = Path(PROJECT_ROOT) / "data" / "reports"


def _fmt_seconds(seconds: float | int | None) -> str:
    seconds = float(seconds or 0)
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, sec = divmod(int(seconds), 60)
    return f"{minutes}m{sec:02d}s"


def write_workflow_report(
    results: list[dict[str, Any]],
    started_at: datetime,
    elapsed_seconds: float,
    dry_run: bool = False,
    config_warnings: list[str] | None = None,
    ai_health: dict[str, Any] | None = None,
    notes: list[str] | None = None,
) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    path = REPORT_DIR / f"daily_run_{now.strftime('%Y%m%d_%H%M%S')}.md"

    success_count = sum(1 for item in results if item.get("success"))
    failed_count = len(results) - success_count
    lines = [
        f"# 每日运行摘要 {now.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 总览",
        "",
        f"- 运行模式: {'dry-run 预演' if dry_run else '正式运行'}",
        f"- 开始时间: {started_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 总用时: {_fmt_seconds(elapsed_seconds)}",
        f"- 步骤数: {len(results)}",
        f"- 成功: {success_count}",
        f"- 失败: {failed_count}",
        "",
    ]

    if ai_health:
        lines += [
            "## AI 预检",
            "",
            f"- 状态: {'通过' if ai_health.get('ok') else '失败'}",
            f"- 提供商: {ai_health.get('provider', '')}",
            f"- 模型: {ai_health.get('model', '')}",
            f"- 用时: {_fmt_seconds(ai_health.get('duration_seconds'))}",
        ]
        if ai_health.get("error"):
            lines.append(f"- 错误: {ai_health.get('error')}")
        lines.append("")

    if config_warnings:
        lines += ["## 配置提醒", ""]
        lines.extend(f"- {item}" for item in config_warnings)
        lines.append("")

    if notes:
        lines += ["## 运行备注", ""]
        lines.extend(f"- {item}" for item in notes)
        lines.append("")

    lines += ["## 步骤结果", ""]
    for item in results:
        status = "成功" if item.get("success") else "失败"
        lines.append(f"- 步骤 {item.get('step')}: {item.get('name')} | {status} | {_fmt_seconds(item.get('elapsed'))}")
        if item.get("status") and item.get("status") not in {"success", "failed"}:
            lines.append(f"  状态: {item.get('status')}")
        if item.get("returncode") not in {None, 0}:
            lines.append(f"  退出码: {item.get('returncode')}")
        diagnostics = item.get("diagnostics") or {}
        if diagnostics.get("diagnosis"):
            lines.append(f"  诊断: {diagnostics.get('diagnosis')}")
        if item.get("error"):
            lines.append(f"  错误: {str(item.get('error'))[:300]}")

    lines += [
        "",
        "## 限量说明",
        "",
        "- 微信公众号：无历史记录按 `wechat.new_account_download_count`，有历史记录按 `wechat.max_download_per_account`。",
        "- 小宇宙：无历史记录按 `podcast.new_account_download_count`，有历史记录按 `podcast.max_download_per_account`。",
        "- 会议纪要：无历史记录按 `memo.new_source_download_count`，有历史记录按 `memo.max_download_per_source`。",
        "- Notion 微信收藏：默认不设下载上限，会处理当前数据库中所有待下载项。",
    ]

    path.write_text(normalize_markdown_output("\n".join(lines)), encoding="utf-8")
    return path

