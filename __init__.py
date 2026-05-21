"""
Curve-memory plugin — 遗忘曲线记忆系统

基于 R(t) = 0.462 + 0.538 · exp(-t/2.71) 的遗忘曲线，
提供三路混合检索（BM25 + Embedding + R(t)）和双层归档。

安装：hermes plugins install git@github.com:sin1111yi/curve-memory.git
配置：hermes config set memory.plugin curve-memory
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# 将插件自身目录加入 path，以便导入 core 模块
_PLUGIN_DIR = Path(__file__).parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

# 同时将 scripts/ 加入 path 作为后备
_SCRIPTS_DIR = Path.home() / ".hermes" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

logger = logging.getLogger(__name__)

PLUGIN_NAME = "curve-memory"
PLUGIN_VERSION = "1.0.0"

__all__: list[str] = []


def register(ctx):
    """Plugin-style registration — called by Hermes plugin system.

    Registers CurveMemoryProvider, tools, and CLI commands.
    """
    from curve_memory.provider import CurveMemoryProvider

    ctx.register_provider("memory", CurveMemoryProvider)
    ctx.register_cli("curve-memory", {
        "help": "遗忘曲线记忆系统",
        "handler": "curve_memory.cli:main",
    })
    logger.info("Curve-memory plugin registered")


def get_provider():
    """Legacy entry point for memory provider discovery."""
    try:
        from curve_memory.provider import CurveMemoryProvider
        return CurveMemoryProvider()
    except Exception as e:
        logger.debug("CurveMemory get_provider failed: %s", e)
        return None
