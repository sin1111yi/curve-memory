"""
Provider — curve-memory MemoryProvider 实现
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

_PLUGIN_DIR = Path(__file__).parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

logger = logging.getLogger(__name__)

# 工具 schema
TOOL_SCHEMAS = [
    {
        "name": "curve_memory_search",
        "description": (
            "三路混合检索记忆系统（BM25 + Embedding qwen3-embedding:8b + R(t) 遗忘曲线）。"
            "返回 top-5 相关记忆及 TIER 级别。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "检索关键词或自然语言描述",
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回结果数（默认 5）",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
]


def _touch_memory(topic: str):
    try:
        from curve_memory.core.activity import parse_activity, format_activity
        activity_path = Path.home() / ".hermes" / "memories" / "ACTIVITY.yaml"
        if not activity_path.exists():
            return
        raw = activity_path.read_text(encoding="utf-8")
        data = parse_activity(raw)
        memories = data.get("memories", {})
        if topic in memories:
            memories[topic]["t"] = 0
            memories[topic]["access_count"] = memories[topic].get("access_count", 0) + 1
            activity_path.write_text(format_activity(data), encoding="utf-8")
    except Exception as e:
        logger.debug("touch error: %s", e)


def _extract_mentioned_topics(text: str) -> list:
    topics = set()
    mem_dir = Path.home() / ".hermes" / "memories" / "active"
    if not mem_dir.exists():
        return []
    for f in mem_dir.glob("*.md"):
        topic = f.stem
        if topic.lower() in text.lower():
            topics.add(topic)
    return list(topics)


try:
    from agent.memory_provider import MemoryProvider

    class CurveMemoryProvider(MemoryProvider):
        """遗忘曲线记忆系统 — 基于 R(t) 遗忘曲线的 MemoryProvider"""

        name = "curve-memory"

        def __init__(self):
            self._embedder = None
            self._searcher = None
            self._touched_topics = set()

        def is_available(self) -> bool:
            return (Path.home() / ".hermes" / "memories" / "ACTIVITY.yaml").exists()

        def initialize(self, session_id: str, **kwargs):
            try:
                from curve_memory.core.embedding_provider import create_embedding_provider
                self._embedder = create_embedding_provider()
            except Exception:
                self._embedder = None
            try:
                from curve_memory.core.search import HybridSearch
                self._searcher = HybridSearch(
                    Path.home() / ".hermes" / "memories",
                    embedder=self._embedder,
                )
            except Exception:
                self._searcher = None
            self._touched_topics = set()
            logger.debug("CurveMemory init (deg=%s)",
                         getattr(self._searcher, 'degrade_level', 'N/A'))

        def prefetch(self, query: str, *, session_id: str = "") -> str:
            if not self._searcher or not query.strip():
                return ""
            try:
                results = self._searcher.search(query, top_k=3)
                if not results:
                    return ""
                blocks = []
                for topic, score, snippet, r in results:
                    from curve_memory.core.tier import r_to_tier_name
                    tier = r_to_tier_name(r)
                    blocks.append(f"### {topic} ({tier})\n{snippet}")
                    self._touched_topics.add(topic)
                return "## 召回记忆\n\n" + "\n\n".join(blocks)
            except Exception as e:
                logger.debug("prefetch error: %s", e)
                return ""

        def sync_turn(self, user: str, asst: str):
            mentioned = _extract_mentioned_topics(user)
            mentioned += _extract_mentioned_topics(asst)
            for topic in set(mentioned) | self._touched_topics:
                _touch_memory(topic)
            self._touched_topics.clear()

        def get_tool_schemas(self) -> list:
            return TOOL_SCHEMAS

        def system_prompt_block(self) -> str:
            return (
                "## Memory System\n"
                "R(t) = 0.462 + 0.538*exp(-t/2.71)\n"
                "TIER_5(≤1d) → TIER_4(≤3d) → TIER_3(≤7d) → TIER_2(≤14d) → ARCHIVE(≥30d)"
            )

except ImportError:
    CurveMemoryProvider = None  # Hermes 环境不可用
