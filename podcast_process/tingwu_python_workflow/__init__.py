"""Compatibility package for podcast Tingwu workflow helpers."""

from __future__ import annotations

import importlib
import sys

_new_package = importlib.import_module("investment_system.collectors.podcast.tingwu_python_workflow")
sys.modules[__name__] = _new_package
