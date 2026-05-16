"""Compatibility package for ``investment_system.collectors.notion``."""

from __future__ import annotations

import importlib
import sys

_new_package = importlib.import_module("investment_system.collectors.notion")
sys.modules[__name__] = _new_package
