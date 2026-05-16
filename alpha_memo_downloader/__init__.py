"""Compatibility package for ``investment_system.collectors.alpha_memo``."""

from __future__ import annotations

import importlib
import sys

_new_package = importlib.import_module("investment_system.collectors.alpha_memo")
sys.modules[__name__] = _new_package
