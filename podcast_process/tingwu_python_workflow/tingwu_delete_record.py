"""Compatibility wrapper for Tingwu delete-record helpers."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

if __name__ == "__main__":
    runpy.run_module("investment_system.collectors.podcast.tingwu_python_workflow.tingwu_delete_record", run_name="__main__")
else:
    from investment_system.collectors.podcast.tingwu_python_workflow.tingwu_delete_record import *  # noqa: F401,F403
