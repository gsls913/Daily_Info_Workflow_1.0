"""Compatibility package for the renamed shared library.

New code should import from ``investment_system.common``.
"""

from __future__ import annotations

import importlib
import sys

_new_package = importlib.import_module("investment_system.common")
sys.modules[__name__] = _new_package
