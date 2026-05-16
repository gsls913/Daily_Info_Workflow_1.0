"""Compatibility package for ``investment_system.collectors.podcast``."""

from __future__ import annotations

import importlib
import sys

_new_package = importlib.import_module("investment_system.collectors.podcast")
sys.modules[__name__] = _new_package
