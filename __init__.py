"""
Curve-memory plugin — forgetting curve memory system

Provides R(t) = 0.462 + 0.538 * exp(-t/2.71) based memory with
triple hybrid search (BM25 + Embedding + R(t)) and dual-tier archiving.
"""

from __future__ import annotations

import logging

from curve_memory.provider import CurveMemoryProvider

logger = logging.getLogger(__name__)

PLUGIN_NAME = "curve-memory"
PLUGIN_VERSION = "1.0.0"


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
