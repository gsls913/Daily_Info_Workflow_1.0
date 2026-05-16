"""Compatibility package for ``investment_system.micro_programs``."""

from __future__ import annotations

import importlib
import sys

_new_package = importlib.import_module("investment_system.micro_programs")
sys.modules[__name__] = _new_package
