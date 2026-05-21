"""
CurveMemoryProvider — 完整的 Hermes MemoryProvider 实现

生命周期：
  initialize() → 创建资源，加载配置和嵌入引擎
  prefetch()   → 每轮对话前召回记忆
  sync_turn()  → 每轮对话后更新活性
  get_tool_schemas() / handle_tool_call() → 暴露搜索工具
  get_config_schema() / save_config() → hermes memory setup 支持
  shutdown()   → 清理
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 工具 schema
TOOL_SCHEMAS = [
    {
        "name": "curve_memory_search",
        "description": (
            "Hybrid search across persistent memories stored in the curve-memory system. "
            "Uses BM25 + embedding similarity + recency scoring (forgetting curve). "
            "Returns top-k relevant memories with their TIER levels."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query or natural language description",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (default: 5, max: 20)",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
]


def _touch_memory(topic: str, memories_dir: Path):
    """更新记忆的访问时间"""
    try:
        from curve_memory.core.activity import parse_activity, format_activity
        activity_path = memories_dir / "ACTIVITY.yaml"
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


def _extract_mentioned_topics(text: str, memories_dir: Path) -> list:
    """从文本中提取提到的记忆主题"""
    topics = set()
    active_dir = memories_dir / "active"
    if not active_dir.exists():
        return []
    for f in active_dir.glob("*.md"):
        topic = f.stem
        if re.search(r'\b' + re.escape(topic.lower()) + r'\b', text.lower()):
            topics.add(topic)
    return list(topics)


try:
    from agent.memory_provider import MemoryProvider

    class CurveMemoryProvider(MemoryProvider):
        """遗忘曲线记忆系统 — 基于 R(t) 遗忘曲线的 MemoryProvider"""

        name = "curve-memory"

        def __init__(self):
            self._cfg: dict = {}
            self._base: Optional[Path] = None
            self._memories_dir: Optional[Path] = None
            self._embedder = None
            self._searcher = None
            self._touched_topics: set = set()

        # ── Core lifecycle ──────────────────────────────────────────────

        def is_available(self) -> bool:
            """本地插件始终可用（只要有配置路径）"""
            return True

        def initialize(self, session_id: str, **kwargs):
            """初始化嵌入引擎、搜索器"""
            hermes_home = kwargs.get("hermes_home", str(Path.home() / ".hermes"))
            self._base = Path(hermes_home)
            self._memories_dir = self._base / "memories"

            # 加载配置
            from curve_memory.core.config import load_config
            self._cfg = load_config(hermes_home)

            # 初始化嵌入器
            try:
                from curve_memory.core.embedding_provider import create_embedding_provider
                self._embedder = create_embedding_provider(self._cfg.get("embedding", {}))
            except Exception as e:
                logger.debug("Embedder init failed: %s", e)
                self._embedder = None

            # 初始化搜索器
            try:
                from curve_memory.core.search import HybridSearch
                self._searcher = HybridSearch(
                    self._memories_dir,
                    embedder=self._embedder,
                    alpha=self._cfg.get("search", {}).get("alpha", 0.35),
                    beta=self._cfg.get("search", {}).get("beta", 0.45),
                    gamma=self._cfg.get("search", {}).get("gamma", 0.20),
                )
            except Exception as e:
                logger.debug("Searcher init failed: %s", e)
                self._searcher = None

            self._touched_topics = set()
            logger.debug("CurveMemory init (deg=%s)", 
                         getattr(self._searcher, 'degrade_level', 'N/A'))

        # ── Config ──────────────────────────────────────────────────────

        def get_config_schema(self) -> List[Dict[str, Any]]:
            from curve_memory.core.config import get_config_schema
            return get_config_schema()

        def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
            from curve_memory.core.config import schema_values_to_config, save_config as _save
            cfg = schema_values_to_config(values)
            _save(cfg, hermes_home)

        # ── System prompt ───────────────────────────────────────────────

        def system_prompt_block(self) -> str:
            return (
                "You have access to a memory system via curve_memory_search tool. "
                "Retrieved memories show a TIER level: TIER_5 (recent, ≤1d), "
                "TIER_4 (≤3d), TIER_3 (≤7d), TIER_2 (≤14d), ARCHIVE (≥30d)."
            )

        # ── Prefetch (context injection) ────────────────────────────────

        def prefetch(self, query: str, *, session_id: str = "") -> str:
            """每轮对话前召回相关记忆"""
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

                return "\n\n## Retrieved Memories\n\n" + "\n\n".join(blocks)
            except Exception as e:
                logger.debug("prefetch error: %s", e)
                return ""

        # ── Sync turn ───────────────────────────────────────────────────

        def sync_turn(self, user: str, asst: str, *, session_id: str = ""):
            """每轮对话后更新被引用的记忆活性"""
            if not self._memories_dir:
                return
            mentioned = _extract_mentioned_topics(user, self._memories_dir)
            mentioned += _extract_mentioned_topics(asst, self._memories_dir)
            for topic in set(mentioned) | self._touched_topics:
                _touch_memory(topic, self._memories_dir)
            self._touched_topics.clear()

        # ── Tools ───────────────────────────────────────────────────────

        def get_tool_schemas(self) -> List[Dict[str, Any]]:
            return TOOL_SCHEMAS

        def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
            if tool_name == "curve_memory_search":
                query = args.get("query", "").strip()
                top_k = min(int(args.get("top_k", 5)), 20)
                if not query:
                    return json.dumps({"error": "No query provided"})
                if not self._searcher:
                    return json.dumps({"error": "Search not initialized"})
                try:
                    results = self._searcher.search(query, top_k=top_k)
                    serializable = [
                        {
                            "topic": topic,
                            "score": round(score, 4),
                            "snippet": snippet[:500],
                            "tier": tier,
                        }
                        for topic, score, snippet, r in results
                        for tier in [__import__(
                            "curve_memory.core.tier", fromlist=["r_to_tier_name"]
                        ).r_to_tier_name(r)]
                    ]
                    return json.dumps({"results": serializable})
                except Exception as e:
                    return json.dumps({"error": str(e)})

            raise NotImplementedError(f"Unknown tool: {tool_name}")

        # ── Shutdown ────────────────────────────────────────────────────

        def shutdown(self):
            self._searcher = None
            self._embedder = None
            self._cfg = {}
            logger.debug("CurveMemory shut down")

except ImportError:
    CurveMemoryProvider = None  # Hermes 环境不可用时降级
