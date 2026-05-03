from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from common_libs.config.config_loader import get as cfg
from common_libs.storage.download_history import save_json_atomic
from common_libs.utils.paths import PROJECT_ROOT, WORKFLOW_ERROR_LOG_FILE


LogFunc = Callable[[str], None]


@dataclass
class CleanupResult:
    label: str
    deleted: int = 0
    trimmed: int = 0
    skipped: bool = False
    note: str = ""


def _project_path(*parts: str) -> Path:
    return Path(PROJECT_ROOT).joinpath(*parts)


def _is_inside_project(path: Path) -> bool:
    try:
        path.resolve().relative_to(Path(PROJECT_ROOT).resolve())
        return True
    except ValueError:
        return False


def _delete_file(path: Path) -> bool:
    if not path.is_file() or not _is_inside_project(path):
        return False
    try:
        path.unlink()
        return True
    except Exception:
        return False


def _delete_by_age(directory: Path, pattern: str, days: int) -> int:
    if days < 0 or not directory.exists():
        return 0
    cutoff = time.time() - days * 24 * 3600
    deleted = 0
    for path in directory.glob(pattern):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff and _delete_file(path):
                deleted += 1
        except Exception:
            continue
    return deleted


def _limit_file_count(directory: Path, pattern: str, max_files: int) -> int:
    if max_files <= 0 or not directory.exists():
        return 0
    files = [p for p in directory.glob(pattern) if p.is_file()]
    if len(files) <= max_files:
        return 0
    files.sort(key=lambda p: p.stat().st_mtime)
    deleted = 0
    for path in files[: len(files) - max_files]:
        if _delete_file(path):
            deleted += 1
    return deleted


def _log_dirs() -> list[Path]:
    return [
        _project_path("alpha_memo_downloader", "logs"),
        _project_path("alpha_wechat_downloader", "logs"),
        _project_path("notion_wechat_downloader", "logs"),
        _project_path("podcast_process", "logs"),
        _project_path("common_libs", "wechat_downloader", "logs"),
        _project_path("tools", "get_all_memo_of_one_account", "logs"),
        _project_path("data", "logs"),
    ]


def clean_logs() -> CleanupResult:
    log_days = int(cfg("retention.log_days", 30))
    max_files = int(cfg("retention.log_max_files_per_dir", 90))
    deleted = 0
    for directory in _log_dirs():
        deleted += _delete_by_age(directory, "*.log", log_days)
        deleted += _limit_file_count(directory, "*.log", max_files)
    return CleanupResult("日志文件", deleted=deleted)


def clean_reports() -> CleanupResult:
    directory = _project_path("data", "reports")
    report_days = int(cfg("retention.report_days", 90))
    max_files = int(cfg("retention.report_max_files", 120))
    deleted = _delete_by_age(directory, "daily_run_*.md", report_days)
    deleted += _limit_file_count(directory, "daily_run_*.md", max_files)
    return CleanupResult("每日运行摘要", deleted=deleted)


def clean_tmp_files() -> CleanupResult:
    tmp_days = int(cfg("retention.tmp_days", 3))
    deleted = 0
    for directory in [
        _project_path("data", "logs"),
        _project_path("data", "history"),
        _project_path("data", "reports"),
        _project_path("data", "config"),
        _project_path("data", "podcast"),
        _project_path("common_libs"),
        _project_path("workflow"),
        _project_path("podcast_process"),
    ]:
        if not directory.exists():
            continue
        deleted += _delete_by_age(directory, "**/*.tmp", tmp_days)
        deleted += _delete_by_age(directory, "**/*.tmp.*", tmp_days)
    return CleanupResult("临时文件", deleted=deleted)


def trim_workflow_error_log() -> CleanupResult:
    path = Path(WORKFLOW_ERROR_LOG_FILE)
    max_kb = int(cfg("retention.workflow_error_log_max_kb", 1024))
    if max_kb <= 0 or not path.exists():
        return CleanupResult("主错误日志")

    max_bytes = max_kb * 1024
    try:
        current_size = path.stat().st_size
        if current_size <= max_bytes:
            return CleanupResult("主错误日志")
        keep_bytes = max_bytes // 2
        with open(path, "rb") as f:
            f.seek(max(0, current_size - keep_bytes))
            tail = f.read()
        marker = (
            f"\n\n--- 日志已自动截断，仅保留最近约 {keep_bytes // 1024}KB，"
            f"原大小 {current_size // 1024}KB ---\n\n"
        ).encode("utf-8")
        path.write_bytes(marker + tail)
        return CleanupResult("主错误日志", trimmed=1, note=f"{current_size // 1024}KB -> {path.stat().st_size // 1024}KB")
    except Exception as exc:
        return CleanupResult("主错误日志", note=f"截断失败: {exc}")


def prune_task_state() -> CleanupResult:
    path = _project_path("data", "history", "task_state.json")
    if not path.exists():
        return CleanupResult("任务状态")

    days = int(cfg("retention.task_state_days", 120))
    max_entries = int(cfg("retention.task_state_max_entries", 5000))
    cutoff = time.time() - days * 24 * 3600

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        tasks = data.get("tasks", {})
        if not isinstance(tasks, dict):
            return CleanupResult("任务状态")

        def updated_ts(item: dict) -> float:
            value = item.get("updated_at") or ""
            try:
                return time.mktime(time.strptime(value[:19], "%Y-%m-%dT%H:%M:%S"))
            except Exception:
                try:
                    return path.stat().st_mtime
                except Exception:
                    return time.time()

        kept_items = []
        removed = 0
        for task_id, task in tasks.items():
            if not isinstance(task, dict):
                removed += 1
                continue
            ts = updated_ts(task)
            if days >= 0 and ts < cutoff and task.get("status") not in {"running", "failed"}:
                removed += 1
                continue
            kept_items.append((task_id, task, ts))

        if max_entries > 0 and len(kept_items) > max_entries:
            kept_items.sort(key=lambda item: item[2], reverse=True)
            removed += len(kept_items) - max_entries
            kept_items = kept_items[:max_entries]

        if removed:
            data["tasks"] = {task_id: task for task_id, task, _ in kept_items}
            data["cleaned_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            save_json_atomic(data, path)
        return CleanupResult("任务状态", deleted=removed)
    except Exception as exc:
        return CleanupResult("任务状态", note=f"清理失败: {exc}")


def run_startup_maintenance(log_func: LogFunc = print) -> list[CleanupResult]:
    if not cfg("safety.allow_local_delete", True):
        result = CleanupResult("运行产物清理", skipped=True, note="safety.allow_local_delete=false")
        log_func(f"🧹 跳过运行产物清理: {result.note}")
        return [result]

    results = [
        clean_logs(),
        clean_reports(),
        clean_tmp_files(),
        trim_workflow_error_log(),
        prune_task_state(),
    ]
    changed = [r for r in results if r.deleted or r.trimmed or r.note]
    if changed:
        log_func("\n🧹 运行产物清理:")
        for item in changed:
            parts = []
            if item.deleted:
                parts.append(f"删除 {item.deleted}")
            if item.trimmed:
                parts.append("截断")
            if item.note:
                parts.append(item.note)
            log_func(f"   - {item.label}: {', '.join(parts)}")
    return results
