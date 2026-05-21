"""
Curve-memory plugin — 遗忘曲线记忆系统

基于 R(t) = 0.462 + 0.538 · exp(-t/2.71) 的遗忘曲线，
提供三路混合检索（BM25 + Embedding + R(t)）和双层归档。

安装：hermes plugins install https://github.com/sin1111yi/curve-memory.git
初始化：hermes curve-memory setup（交互式配置）
启用：hermes config set memory.provider curve-memory && hermes gateway restart
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

PLUGIN_NAME = "curve-memory"
PLUGIN_VERSION = "1.0.0"

# 确保子模块可导入
_PLUGIN_DIR = Path(__file__).parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

# 暴露 MemoryProvider 子类（供 _load_provider_from_dir 发现）
from curve_memory.provider import CurveMemoryProvider


def register(ctx):
    """Called by Hermes plugin system — register as memory provider."""
    ctx.register_memory_provider(CurveMemoryProvider())
    logger.info("Curve-memory registered as memory provider")


def get_provider():
    """Legacy entry point for memory provider discovery."""
    try:
        return CurveMemoryProvider()
    except Exception as e:
        logger.debug("get_provider failed: %s", e)
        return None
