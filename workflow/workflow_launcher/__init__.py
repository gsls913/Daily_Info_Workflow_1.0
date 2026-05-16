"""Compatibility package for ``investment_system.launcher``."""

from __future__ import annotations

import importlib
import sys

_new_package = importlib.import_module("investment_system.launcher")
sys.modules[__name__] = _new_package
