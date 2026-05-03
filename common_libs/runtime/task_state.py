from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from common_libs.storage.download_history import save_json_atomic
from common_libs.utils.paths import PROJECT_ROOT


STATE_FILE = Path(PROJECT_ROOT) / "data" / "history" / "task_state.json"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_task_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"tasks": {}}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        data.setdefault("tasks", {})
        return data
    except Exception:
        return {"tasks": {}}


def save_task_state(state: dict[str, Any]) -> None:
    save_json_atomic(state, STATE_FILE)


def make_task_id(workflow: str, key: str) -> str:
    return f"{workflow}:{key}"


def update_task(
    workflow: str,
    key: str,
    status: str,
    title: str = "",
    meta: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    state = load_task_state()
    tasks = state.setdefault("tasks", {})
    task_id = make_task_id(workflow, key)
    existing = tasks.get(task_id, {})
    history = existing.get("history", [])
    history.append({"status": status, "at": _now(), "error": error})
    tasks[task_id] = {
        "workflow": workflow,
        "key": key,
        "title": title or existing.get("title", ""),
        "status": status,
        "meta": {**(existing.get("meta") or {}), **(meta or {})},
        "error": error,
        "updated_at": _now(),
        "history": history[-20:],
    }
    save_task_state(state)
