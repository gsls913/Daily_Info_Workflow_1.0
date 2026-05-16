from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any

from investment_system.common.storage.download_history import load_json_with_backup, save_json_atomic
from investment_system.common.utils.paths import LAST_DOWNLOAD_MARKDOWNS_FILE


CATEGORY_ALPHA_MEMO = "alpha_memo"
CATEGORY_WECHAT = "wechat"
CATEGORY_PODCAST = "podcast"

KNOWN_CATEGORIES = (CATEGORY_ALPHA_MEMO, CATEGORY_WECHAT, CATEGORY_PODCAST)

_LOCK = Lock()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def empty_manifest() -> dict[str, Any]:
    return {
        "version": 1,
        "run_started_at": _now(),
        "updated_at": _now(),
        "categories": {category: [] for category in KNOWN_CATEGORIES},
    }


def start_new_manifest() -> dict[str, Any]:
    manifest = empty_manifest()
    save_json_atomic(manifest, LAST_DOWNLOAD_MARKDOWNS_FILE)
    return manifest


def load_manifest() -> dict[str, Any]:
    manifest = load_json_with_backup(LAST_DOWNLOAD_MARKDOWNS_FILE, default=empty_manifest())
    if not isinstance(manifest, dict):
        manifest = empty_manifest()
    categories = manifest.setdefault("categories", {})
    for category in KNOWN_CATEGORIES:
        if not isinstance(categories.get(category), list):
            categories[category] = []
    return manifest


def save_manifest(manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = _now()
    save_json_atomic(manifest, LAST_DOWNLOAD_MARKDOWNS_FILE)


def record_markdown(category: str, md_path: str | Path, title: str = "", meta: dict[str, Any] | None = None) -> None:
    if category not in KNOWN_CATEGORIES:
        raise ValueError(f"Unknown download category: {category}")
    if not md_path:
        return
    path = str(Path(md_path))
    entry = {
        "path": path,
        "title": title or Path(path).stem,
        "recorded_at": _now(),
        "meta": meta or {},
    }
    with _LOCK:
        manifest = load_manifest()
        items = manifest.setdefault("categories", {}).setdefault(category, [])
        if not any(item.get("path") == path for item in items if isinstance(item, dict)):
            items.append(entry)
            save_manifest(manifest)

