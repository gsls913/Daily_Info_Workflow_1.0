"""Compatibility wrapper for Tingwu login probe helpers."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

if __name__ == "__main__":
    runpy.run_module("investment_system.collectors.podcast.tingwu_python_workflow.tingwu_login_probe", run_name="__main__")
else:
    from investment_system.collectors.podcast.tingwu_python_workflow.tingwu_login_probe import *  # noqa: F401,F403
