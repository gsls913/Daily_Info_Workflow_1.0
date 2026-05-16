from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from investment_system.common.storage.download_history import load_json_with_backup, save_json_atomic
from investment_system.common.utils.paths import PROJECT_ROOT


STATE_FILE = Path(PROJECT_ROOT) / "data" / "history" / "task_state.json"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_task_state() -> dict[str, Any]:
    data = load_json_with_backup(STATE_FILE, default={"tasks": {}})
    if not isinstance(data, dict):
        return {"tasks": {}}
    data.setdefault("tasks", {})
    return data


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

