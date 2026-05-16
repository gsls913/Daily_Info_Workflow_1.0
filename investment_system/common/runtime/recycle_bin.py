from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from investment_system.common.config.config_loader import get as cfg
from investment_system.common.storage.download_history import save_json_atomic


DEFAULT_RECYCLE_BIN_DIR = r"D:\softwares\Obsidian\MyNotes\信息收集器\_overall\_recycle_bin"
LogFunc = Callable[[str], None]


@dataclass
class RecycleMove:
    source: Path
    destination: Path
    category: str
    item_type: str


def recycle_bin_root() -> Path:
    raw = (
        os.environ.get("INFO_COLLECTOR_RECYCLE_BIN_DIR")
        or cfg("safety.recycle_bin_dir", DEFAULT_RECYCLE_BIN_DIR)
        or DEFAULT_RECYCLE_BIN_DIR
    )
    return Path(str(raw)).expanduser()


def recycle_retention_days() -> int:
    try:
        return int(cfg("safety.recycle_bin_retention_days", 10))
    except Exception:
        return 10


def _safe_part(value: str) -> str:
    value = (value or "unknown").strip()
    for char in '<>:"/\\|?*':
        value = value.replace(char, "_")
    return value or "unknown"


def _unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for idx in range(1, 10000):
        candidate = path.with_name(f"{stem}_{idx}{suffix}")
        if not candidate.exists():
            return candidate
    timestamp = datetime.now().strftime("%H%M%S%f")
    return path.with_name(f"{stem}_{timestamp}{suffix}")


def move_to_recycle_bin(
    path: str | Path,
    *,
    category: str,
    item_type: str,
    log_func: LogFunc | None = None,
) -> RecycleMove:
    source = Path(path).resolve()
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(str(source))

    today = datetime.now().strftime("%Y%m%d")
    target_dir = recycle_bin_root() / _safe_part(category) / _safe_part(item_type) / today
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = _unique_destination(target_dir / source.name)
    shutil.move(str(source), str(destination))
    now = datetime.now()
    os.utime(destination, None)

    manifest_path = target_dir / "_manifest.json"
    entry = {
        "source": str(source),
        "destination": str(destination),
        "category": category,
        "item_type": item_type,
        "moved_at": now.isoformat(timespec="seconds"),
    }
    try:
        if manifest_path.exists():
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                data = []
        else:
            data = []
        data.append(entry)
        save_json_atomic(data, manifest_path)
    except Exception as exc:
        if log_func:
            log_func(f"回收站 manifest 写入失败: {exc}")

    return RecycleMove(
        source=source,
        destination=destination,
        category=category,
        item_type=item_type,
    )


def _manifest_move_times(root: Path) -> dict[Path, float]:
    move_times: dict[Path, float] = {}
    for manifest_path in root.rglob("_manifest.json"):
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for entry in data:
            if not isinstance(entry, dict):
                continue
            destination = entry.get("destination")
            moved_at = entry.get("moved_at")
            if not destination or not moved_at:
                continue
            try:
                moved_ts = datetime.fromisoformat(str(moved_at)[:19]).timestamp()
            except Exception:
                continue
            move_times[Path(destination).resolve()] = moved_ts
    return move_times


def purge_recycle_bin(retention_days: int | None = None) -> int:
    root = recycle_bin_root()
    days = recycle_retention_days() if retention_days is None else int(retention_days)
    if days < 0 or not root.exists():
        return 0

    cutoff = time.time() - days * 24 * 3600
    manifest_move_times = _manifest_move_times(root)
    deleted = 0
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        try:
            if path.name == "_manifest.json":
                continue
            moved_ts = manifest_move_times.get(path.resolve())
            age_ts = moved_ts if moved_ts is not None else path.stat().st_mtime
            if path.is_file() and age_ts < cutoff:
                path.unlink()
                deleted += 1
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        except Exception:
            continue
    for manifest_path in sorted(root.rglob("_manifest.json"), key=lambda item: len(item.parts), reverse=True):
        try:
            if not any(item.name != "_manifest.json" for item in manifest_path.parent.iterdir()):
                manifest_path.unlink()
        except Exception:
            continue
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        try:
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        except Exception:
            continue
    return deleted

