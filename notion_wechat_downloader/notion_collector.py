"""Compatibility wrapper for the Notion WeChat collector entrypoint."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

if __name__ == "__main__":
    runpy.run_module("investment_system.collectors.notion.notion_collector", run_name="__main__")
else:
    from investment_system.collectors.notion.notion_collector import *  # noqa: F401,F403
