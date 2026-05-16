"""Compatibility package for ``investment_system.common.wechat_downloader``."""

from __future__ import annotations

import importlib
import sys

_new_package = importlib.import_module("investment_system.common.wechat_downloader")
sys.modules[__name__] = _new_package
