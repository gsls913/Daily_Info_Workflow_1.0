from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class TaskResult:
    """Normalized runtime result for one workflow task."""

    step: int
    name: str
    status: str
    success: bool
    elapsed: float = 0.0
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    returncode: int | None = None
    outputs: list[str] = field(default_factory=list)
    diagnostics: dict[str, Any] | None = None

    @classmethod
    def started(cls, step: int, name: str) -> "TaskResult":
        return cls(
            step=step,
            name=name,
            status="running",
            success=False,
            started_at=datetime.now().isoformat(timespec="seconds"),
        )

    @classmethod
    def from_execution(
        cls,
        *,
        step: int,
        name: str,
        success: bool,
        elapsed: float,
        error: str | None = None,
        returncode: int | None = None,
        outputs: list[str] | None = None,
        diagnostics: dict[str, Any] | None = None,
        started_at: str | None = None,
    ) -> "TaskResult":
        return cls(
            step=step,
            name=name,
            status="success" if success else "failed",
            success=success,
            elapsed=elapsed,
            error=error,
            started_at=started_at,
            finished_at=datetime.now().isoformat(timespec="seconds"),
            returncode=returncode,
            outputs=outputs or [],
            diagnostics=diagnostics,
        )

    def to_report_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["error"] = self.error
        return data


def normalize_result_dict(result: TaskResult | dict[str, Any]) -> dict[str, Any]:
    if isinstance(result, TaskResult):
        return result.to_report_dict()
    return result

