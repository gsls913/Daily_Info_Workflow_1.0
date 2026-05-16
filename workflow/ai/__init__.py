"""Compatibility package for legacy ``workflow.ai`` imports."""

from __future__ import annotations

import importlib
import sys

_new_package = importlib.import_module("investment_system.workflow_ai")
sys.modules[__name__] = _new_package
