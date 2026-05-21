"""
CurveMemoryProvider — 完整的 Hermes MemoryProvider 实现

生命周期：
  initialize() → 创建资源，加载配置、嵌入引擎、用户画像
  prefetch()   → 每轮对话前召回记忆 + 用户画像
  sync_turn()  → 每轮对话后更新活性
  get_tool_schemas() / handle_tool_call() → 搜索 + 用户画像工具
  get_config_schema() / save_config() → hermes memory setup 支持
  on_session_end() → 惰性归档
  shutdown()   → 清理
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider

from curve_memory.core.config import get_config_schema, load_config, schema_values_to_config, save_config
from curve_memory.core.embedding import create_embedding_provider
from curve_memory.core.search import HybridSearch
from curve_memory.core.activity import parse_activity, format_activity, load_activity
from curve_memory.core.tier import forgetting_curve, r_to_tier_name

logger = logging.getLogger(__name__)

# ── Tool schemas ─────────────────────────────────────────────────────

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
    {
        "name": "curve_memory_user_get",
        "description": "Get all stored user profile entries. Returns key-value pairs the agent knows about the user.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "curve_memory_user_set",
        "description": "Store a fact about the user in the user profile. Use this to remember user preferences, personal details, communication style, and other user-specific information that persists across sessions.",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "A short key name (e.g. 'preferred_language', 'timezone', 'likes')",
                },
                "value": {
                    "type": "string",
                    "description": "The value to store",
                },
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "curve_memory_user_delete",
        "description": "Remove a fact from the user profile.",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "The key to remove from user profile",
                },
            },
            "required": ["key"],
        },
    },
]


# ── Helpers ──────────────────────────────────────────────────────────

def _touch_memory(topic: str, memories_dir: Path):
    """更新记忆的访问时间为当前 Unix 时间戳"""
    try:
        activity_path = memories_dir / "ACTIVITY.yaml"
        if not activity_path.exists():
            return
        raw = activity_path.read_text(encoding="utf-8")
        data = parse_activity(raw)
        memories = data.get("memories", {})
        if topic in memories:
            memories[topic]["t"] = int(time.time())
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


# ── Provider ─────────────────────────────────────────────────────────

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

        # 用户画像（USER.md 格式）
        self._user_profile: Dict[str, str] = {}
        self._user_profile_path: Optional[Path] = None

    # ── Core lifecycle ──────────────────────────────────────────────

    def is_available(self) -> bool:
        """本地插件始终可用"""
        return True

    def initialize(self, session_id: str, **kwargs):
        """初始化嵌入引擎、搜索器、用户画像"""
        hermes_home = kwargs.get("hermes_home", str(Path.home() / ".hermes"))
        self._base = Path(hermes_home)
        self._memories_dir = self._base / "memories"

        # 加载配置
        self._cfg = load_config(hermes_home)

        # 加载用户画像（USER.md 格式）
        self._user_profile_path = self._base / "memories" / "USER.md"
        self._load_user_profile()

        # 初始化嵌入器
        try:
            self._embedder = create_embedding_provider(self._cfg.get("embedding", {}))
        except Exception as e:
            logger.debug("Embedder init failed: %s", e)
            self._embedder = None

        # 初始化搜索器
        try:
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

        # 迁移旧格式 t 值
        self._migrate_t_values()

        # 惰性归档
        self._archive_sweep()

        # 清理旧的 cron 任务
        self._cleanup_old_cron()

        self._touched_topics = set()
        logger.debug("CurveMemory init (deg=%s, user_profile=%d entries)",
                     getattr(self._searcher, 'degrade_level', 'N/A'),
                     len(self._user_profile))

    # ── User profile (USER.md format) ─────────────────────────────

    def _load_user_profile(self):
        """从磁盘加载用户画像（Markdown key-value 格式）"""
        if self._user_profile_path and self._user_profile_path.exists():
            try:
                self._user_profile = {}
                for line in self._user_profile_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith("---"):
                        continue
                    if ":" in line:
                        key, _, value = line.partition(":")
                        self._user_profile[key.strip()] = value.strip()
            except Exception as e:
                logger.debug("User profile load error: %s", e)
                self._user_profile = {}
        else:
            self._user_profile = {}

    def _save_user_profile(self):
        """持久化用户画像为 Markdown 格式"""
        if self._user_profile_path:
            self._user_profile_path.parent.mkdir(parents=True, exist_ok=True)
            lines = ["# User Profile", "", f"# 由 curve-memory 管理，更新于 {time.strftime('%Y-%m-%d %H:%M')}", ""]
            for k, v in sorted(self._user_profile.items()):
                lines.append(f"{k}: {v}")
            self._user_profile_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ── Migration / cleanup ─────────────────────────────────────────

    def _migrate_t_values(self):
        """将旧格式的 t 值（day-counter）迁移为 Unix 时间戳"""
        try:
            activity_path = self._memories_dir / "ACTIVITY.yaml"
            if not activity_path.exists():
                return
            raw = activity_path.read_text(encoding="utf-8")
            data = parse_activity(raw)
            memories = data.get("memories", {})
            changed = False
            now = int(time.time())
            for topic, info in memories.items():
                raw_t = info.get("t", 0)
                if isinstance(raw_t, (int, float)) and 0 < raw_t < 1000000000000:
                    info["t"] = now - raw_t * 86400
                    changed = True
            if changed:
                activity_path.write_text(format_activity(data), encoding="utf-8")
                logger.debug("Migrated t values to timestamps")
        except Exception as e:
            logger.debug("Migration error: %s", e)

    def _cleanup_old_cron(self):
        """清理旧的 cron 任务和脚本"""
        try:
            scripts_dir = self._base / "scripts"
            for name in ["curve-memory-forgetting.py", "curve-memory-indexer.py"]:
                p = scripts_dir / name
                if p.exists():
                    p.unlink()
                    logger.debug("Removed old script: %s", name)
            cron_file = self._base / "cron" / "jobs.json"
            if cron_file.exists():
                data = json.loads(cron_file.read_text())
                before = len(data.get("jobs", []))
                data["jobs"] = [
                    j for j in data.get("jobs", [])
                    if "snowlyn-memory-decay" not in j.get("name", "")
                    and "snowlyn-memory-index" not in j.get("name", "")
                ]
                after = len(data.get("jobs", []))
                if after < before:
                    cron_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
                    logger.debug("Removed %d old cron job(s)", before - after)
        except Exception as e:
            logger.debug("Cron cleanup error: %s", e)

    # ── Archive ─────────────────────────────────────────────────────

    def _archive_sweep(self):
        """惰性归档：扫描所有记忆，归档超过阈值的"""
        try:
            activity_path = self._memories_dir / "ACTIVITY.yaml"
            if not activity_path.exists():
                return
            raw = activity_path.read_text(encoding="utf-8")
            data = parse_activity(raw)
            memories = data.get("memories", {})
            if not memories:
                return
            archive_days = self._cfg.get("tier", {}).get("archive_threshold_days", 30)
            now = time.time()
            changed = False
            to_remove = []
            for topic, info in list(memories.items()):
                raw_t = info.get("t", 0)
                if isinstance(raw_t, (int, float)) and raw_t > 1000000000000:
                    t_days = (now - raw_t) / 86400
                else:
                    t_days = raw_t
                if t_days >= archive_days:
                    if info.get("mature", False):
                        self._mature_archive(topic, info, data)
                    else:
                        self._forget_archive(topic, info, data)
                    to_remove.append(topic)
                    changed = True
            if changed:
                activity_path.write_text(format_activity(data), encoding="utf-8")
                logger.debug("Archive sweep: archived %d topics", len(to_remove))
        except Exception as e:
            logger.debug("Archive sweep error: %s", e)

    def _forget_archive(self, topic: str, info: dict, data: dict):
        import shutil
        src = self._memories_dir / "active" / f"{topic}.md"
        dst = self._memories_dir / "archive" / "forgotten" / f"{topic}.md"
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.exists():
            shutil.move(str(src), str(dst))
        if topic in data.get("memories", {}):
            del data["memories"][topic]
        logger.debug("Forgotten archive: %s", topic)

    def _mature_archive(self, topic: str, info: dict, data: dict):
        import shutil
        from datetime import datetime
        src = self._memories_dir / "active" / f"{topic}.md"
        dst = self._memories_dir / "archive" / "mature" / f"{topic}.md"
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.exists():
            shutil.copy(str(src), str(dst))
        knowledge_dir = self._base / "knowledge"
        knowledge_dir.mkdir(parents=True, exist_ok=True)
        knowledge_path = knowledge_dir / f"{topic}.md"
        original = src.read_text(encoding="utf-8") if src.exists() else ""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        knowledge_content = f"""# {topic}

**来源：** curve-memory mature promotion
**固化时间：** {now_str}
**访问次数：** {info.get('access_count', 0)}
**原始存档：** archive/mature/{topic}.md
**注意：** 此文件由遗忘曲线系统自动生成。

---

{original}
"""
        knowledge_path.write_text(knowledge_content, encoding="utf-8")
        if src.exists():
            src.unlink()
        if topic in data.get("memories", {}):
            del data["memories"][topic]
        logger.debug("Mature archive: %s", topic)

    # ── Config ──────────────────────────────────────────────────────

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return get_config_schema()

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        cfg = schema_values_to_config(values)
        save_config(cfg, hermes_home)

    # ── System prompt ───────────────────────────────────────────────

    def system_prompt_block(self) -> str:
        lines = [
            "You have access to a memory system via curve_memory_search tool. "
            "Retrieved memories show a TIER level: TIER_5 (recent, ≤1d), "
            "TIER_4 (≤3d), TIER_3 (≤7d), TIER_2 (≤14d), ARCHIVE (≥30d)."
        ]
        if self._user_profile:
            entries = "\n".join(f"  {k}: {v}" for k, v in self._user_profile.items())
            lines.append(f"\n## User Profile\n{entries}")
        return "\n".join(lines)

    # ── Prefetch ────────────────────────────────────────────────────

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """每轮对话前召回相关记忆 + 用户画像"""
        parts = []

        # 记忆召回
        if self._searcher and query.strip():
            try:
                results = self._searcher.search(query, top_k=3)
                if results:
                    blocks = []
                    for topic, score, snippet, r in results:
                        tier = r_to_tier_name(r)
                        blocks.append(f"### {topic} ({tier})\n{snippet}")
                        self._touched_topics.add(topic)
                    parts.append("## Retrieved Memories\n\n" + "\n\n".join(blocks))
            except Exception as e:
                logger.debug("prefetch error: %s", e)

        # 用户画像（当查询匹配到画像关键词时注入）
        if self._user_profile and query.strip():
            q_lower = query.lower()
            matched = {k: v for k, v in self._user_profile.items()
                       if k.lower() in q_lower or any(w in q_lower for w in v.lower().split())}
            if matched:
                entries = "\n".join(f"  {k}: {v}" for k, v in matched.items())
                parts.append("## User Profile (matched)\n" + entries)

        return "\n\n".join(parts)

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
                        "tier": r_to_tier_name(r),
                    }
                    for topic, score, snippet, r in results
                ]
                return json.dumps({"results": serializable})
            except Exception as e:
                return json.dumps({"error": str(e)})

        if tool_name == "curve_memory_user_get":
            return json.dumps({"profile": dict(self._user_profile)})

        if tool_name == "curve_memory_user_set":
            key = str(args.get("key", "")).strip()
            value = str(args.get("value", "")).strip()
            if not key:
                return json.dumps({"error": "Key is required"})
            self._user_profile[key] = value
            self._save_user_profile()
            return json.dumps({"status": "ok", "key": key, "value": value})

        if tool_name == "curve_memory_user_delete":
            key = str(args.get("key", "")).strip()
            if key in self._user_profile:
                del self._user_profile[key]
                self._save_user_profile()
                return json.dumps({"status": "deleted", "key": key})
            return json.dumps({"error": f"Key '{key}' not found"})

        raise NotImplementedError(f"Unknown tool: {tool_name}")

    # ── Session end ─────────────────────────────────────────────────

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """对话结束时的惰性归档"""
        self._archive_sweep()

    # ── Shutdown ────────────────────────────────────────────────────

    def shutdown(self):
        self._save_user_profile()
        self._searcher = None
        self._embedder = None
        self._cfg = {}
        logger.debug("CurveMemory shut down")
