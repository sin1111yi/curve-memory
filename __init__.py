"""
Curve-memory plugin — 遗忘曲线记忆系统

基于 R(t) = 0.462 + 0.538 · exp(-t/2.71) 的遗忘曲线，
提供三路混合检索（BM25 + Embedding + R(t)）和双层归档。

安装：hermes plugins install https://github.com/sin1111yi/curve-memory.git
初始化：hermes curve-memory setup
配置：hermes curve-memory config --interactive
启用：hermes config set memory.plugin curve-memory && hermes gateway restart
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

# 暴露 MemoryProvider 子类供发现系统使用
from curve_memory.provider import CurveMemoryProvider


def register(ctx):
    """Plugin-style registration — called by Hermes plugin system.
    Registers CLI subcommand `curve-memory` under `hermes`.
    """
    def setup_fn(subparser):
        """Add all curve-memory subcommands as hermes curve-memory <sub>."""
        from curve_memory.cli import register_subcommands
        register_subcommands(subparser)

    def cli_handler(args):
        """Delegate to curve-memory CLI main()."""
        if hasattr(args, 'func') and args.func:
            args.func(args)
        else:
            from curve_memory.cli import main as curve_main
            curve_main()

    ctx.register_cli_command(
        name="curve-memory",
        help="遗忘曲线记忆系统 — 基于 R(t) 遗忘曲线的记忆管理",
        setup_fn=setup_fn,
        handler_fn=cli_handler,
    )
    logger.info("Curve-memory CLI registered: hermes curve-memory")


def get_provider():
    """Legacy entry point for memory provider discovery."""
    try:
        from curve_memory.provider import CurveMemoryProvider
        return CurveMemoryProvider()
    except Exception as e:
        logger.debug("CurveMemory get_provider failed: %s", e)
        return None
