"""
CurveMemoryProvider — Full Hermes MemoryProvider implementation

Lifecycle:
  initialize() → Create resources, load config, embedding engine, user profile
  prefetch()   → Recall memories + user profile before each turn
  sync_turn()  → Update activity after each turn
  get_tool_schemas() / handle_tool_call() → Search + user profile tools
  get_config_schema() / save_config() → hermes memory setup support
  on_session_end() → Lazy archive sweep
  shutdown()   → Cleanup
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
from curve_memory.enrichment import degradation_sweep, detect_tier_drops, degrade_memory, index_sweep

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
    {
        "name": "curve_memory_enrich",
        "description": (
            "Append new information to an existing memory topic. "
            "Use this when conversation reveals new facts about a known topic. "
            "The topic must already exist in active memories. "
            "New content is appended with a timestamped enriched section. "
            "You should ALWAYS provide a 'summary' parameter — a concise one-line "
            "summary of the memory topic that will be preserved at any TIER level."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "The memory topic name (filename without .md)",
                },
                "content": {
                    "type": "string",
                    "description": "New information to add. Should be concise, factual, in markdown format.",
                },
                "summary": {
                    "type": "string",
                    "description": "A concise one-line summary of the memory topic. ALWAYS provide this — it's preserved at all TIER levels.",
                },
            },
            "required": ["topic", "content", "summary"],
        },
    },
    {
        "name": "curve_memory_degrade_now",
        "description": (
            "Force-degrade all active memories whose content exceeds their TIER's target size. "
            "Called proactively by the agent or automatically during sync_turn()."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "curve_memory_read_note",
        "description": (
            "Load the full content of a detailed note associated with a memory topic. "
            "Use this when you need detailed information that was condensed into a note reference. "
            "Notes are NOT loaded by default; you must explicitly fetch them."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "note_name": {
                    "type": "string",
                    "description": "The note name (without .md extension), e.g. 'searxng-setup-details'",
                },
            },
            "required": ["note_name"],
        },
    },
]


# ── Helpers ──────────────────────────────────────────────────────────

def _touch_memory(topic: str, memories_dir: Path):
    """Update the memory's access time to the current ISO 8601 timestamp"""
    try:
        activity_path = memories_dir / "ACTIVITY.yaml"
        if not activity_path.exists():
            return
        raw = activity_path.read_text(encoding="utf-8")
        data = parse_activity(raw)
        memories = data.get("memories", {})
        if topic in memories:
            from curve_memory.core.activity import format_timestamp
            memories[topic]["t"] = format_timestamp()
            memories[topic]["access_count"] = memories[topic].get("access_count", 0) + 1
            activity_path.write_text(format_activity(data), encoding="utf-8")
    except Exception as e:
        logger.debug("touch error: %s", e)


def _extract_mentioned_topics(text: str, memories_dir: Path) -> list:
    """Extract mentioned memory topics from text"""
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
    """Forgetting-curve memory system — MemoryProvider based on R(t) forgetting curve"""

    name = "curve-memory"

    def __init__(self):
        self._cfg: dict = {}
        self._base: Optional[Path] = None
        self._memories_dir: Optional[Path] = None
        self._embedder = None
        self._searcher = None
        self._touched_topics: set = set()

        # User profile (USER.md format)
        self._user_profile: Dict[str, str] = {}
        self._user_profile_path: Optional[Path] = None

        # index sweep cron job metadata
        self._index_cron_name = "curve-memory-index-sweep"
        self._index_cron_script_name = "curve-memory-index-sweep.py"

        # notes system
        self._notes_dir: Optional[Path] = None

    # ── Core lifecycle ──────────────────────────────────────────────

    def is_available(self) -> bool:
        """Local plugin is always available"""
        return True

    def initialize(self, session_id: str, **kwargs):
        """Initialize embedding engine, searcher, user profile"""
        hermes_home = kwargs.get("hermes_home", str(Path.home() / ".hermes"))
        self._base = Path(hermes_home)
        self._memories_dir = self._base / "memories"

        # Initialize notes directory
        from curve_memory.core.note import get_notes_dir
        self._notes_dir = get_notes_dir(self._base)

        # Load config
        self._cfg = load_config(hermes_home)

        # Load user profile (USER.md format)
        self._user_profile_path = self._base / "memories" / "USER.md"
        self._load_user_profile()

        # Initialize embedder
        try:
            self._embedder = create_embedding_provider(self._cfg.get("embedding", {}))
        except Exception as e:
            logger.debug("Embedder init failed: %s", e)
            self._embedder = None

        # Initialize searcher
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

        # Migrate old-format t values
        self._migrate_t_values()

        # Lazy archive sweep
        self._archive_sweep()

        # Register index sweep cron (idempotent)
        self._register_index_cron()

        # Clean up old cron jobs
        self._cleanup_old_cron()

        self._touched_topics = set()
        logger.debug("CurveMemory init (deg=%s, user_profile=%d entries)",
                     getattr(self._searcher, 'degrade_level', 'N/A'),
                     len(self._user_profile))

    # ── User profile (natural language) ────────────────────────────

    def _load_user_profile(self):
        """Load user profile from disk (natural language + tool entries)

        Compatible with the following formats:
        1. New format: natural language + ## Auto section (key: value entries)
        2. Old format: natural language + § delimiter + key: value lines (OpenClaw legacy)
        3. Plain natural language: no delimiter section at all (fallback: extract key: value patterns from it)
        """
        self._user_profile = {}  # dict for tool ops
        self._user_profile_raw = ""  # raw natural language section
        if not self._user_profile_path or not self._user_profile_path.exists():
            return
        try:
            text = self._user_profile_path.read_text(encoding="utf-8")
            has_auto = "## Auto" in text
            has_section = "§" in text and not has_auto

            if has_auto:
                # New format: ## Auto section
                parts = text.split("## Auto")
                self._user_profile_raw = parts[0].strip()
                if self._user_profile_raw.startswith("# User Profile"):
                    self._user_profile_raw = self._user_profile_raw[len("# User Profile"):].strip()
                if len(parts) > 1:
                    self._parse_kv_lines(parts[1])
            elif has_section:
                # Old format: § delimiter
                parts = text.split("§")
                self._user_profile_raw = parts[0].strip()
                if len(parts) > 1:
                    self._parse_kv_lines(parts[1])
            else:
                # Plain natural language — try to extract key: value patterns from it
                self._user_profile_raw = text.strip()
                self._parse_kv_lines(text)

        except Exception as e:
            logger.debug("User profile load error: %s", e)
            self._user_profile = {}
            self._user_profile_raw = ""

    def _parse_kv_lines(self, text: str):
        """Extract key: value pattern lines from text"""
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            # Skip lines that clearly aren't key:value (contain CJK punctuation, too many spaces)
            k, _, v = line.partition(":")
            k = k.strip()
            v = v.strip()
            if k and v and len(k) < 40 and len(v) < 300:
                self._user_profile[k] = v

    def _save_user_profile(self):
        """Persist user profile: natural language + tool entries.

        Compatibility handling:
        - If _user_profile_raw contains § or duplicate fragments, auto-migrate and clean on first write
        """
        if not self._user_profile_path:
            return
        self._user_profile_path.parent.mkdir(parents=True, exist_ok=True)

        # Clean: if raw contains §, keep only the first half (content after § is already migrated to ## Auto)
        raw = self._user_profile_raw or "# User Profile"
        if "§" in raw:
            raw = raw.split("§")[0].strip()
        raw = raw.strip() or "# User Profile"

        lines = [raw, ""]
        if self._user_profile:
            lines.append("## Auto")
            lines.append("")
            lines.append(f"Updated at {time.strftime('%Y-%m-%d %H:%M')}")
            lines.append("")
            for k, v in sorted(self._user_profile.items()):
                lines.append(f"{k}: {v}")
        self._user_profile_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ── Index sweep cron registration ────────────────────────────

    def _ensure_index_script(self) -> Path:
        """Write index sweep standalone script to ~/.hermes/scripts/, return path"""
        scripts_dir = self._base / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        script_path = scripts_dir / self._index_cron_script_name

        # Only write if the file doesn't already exist
        if script_path.exists():
            return script_path

        plugin_scripts = Path(__file__).parent.parent / "scripts"
        src = plugin_scripts / self._index_cron_script_name
        if src.exists():
            import shutil
            shutil.copy2(str(src), str(script_path))
        else:
            # Fallback: write inline version
            script_path.write_text(f"""#!/usr/bin/env python3
# Auto-generated by curve-memory plugin
import sys, os
from pathlib import Path
sys.path.insert(0, r"{plugin_scripts.parent}")
from curve_memory.enrichment import index_sweep
from curve_memory.core.embedding import create_embedding_provider
hermes_home = Path(os.environ.get("HERMES_HOME", r"{self._base}"))
cfg_json = hermes_home / "curve-memory-config.json"
if cfg_json.exists():
    import json
    cfg = json.loads(cfg_json.read_text())
else:
    cfg = {{"embedding": {{"model": "qwen3-embedding:8b", "base_url": "http://localhost:11434"}}}}
embedder = create_embedding_provider(cfg.get("embedding", {{}}))
if embedder:
    result = index_sweep(hermes_home / "memories", embedder)
    i, c = result.get("indexed", 0), result.get("cleaned", 0)
    if i or c:
        print(f"Index sweep: {{i}} indexed, {{c}} cleaned")
""", encoding="utf-8")
        script_path.chmod(0o755)
        logger.debug("Index sweep script created: %s", script_path)
        return script_path

    def _register_index_cron(self):
        """Register index sweep cron (daily at 03:00), only if not already registered"""
        try:
            cron_dir = self._base / "cron"
            cron_dir.mkdir(parents=True, exist_ok=True)
            cron_file = cron_dir / "jobs.json"

            # Initialize empty jobs.json (file may not exist for new users first cron use)
            if cron_file.exists():
                data = json.loads(cron_file.read_text())
            else:
                data = {"jobs": [], "updated_at": ""}
            jobs = data.get("jobs", [])

            # Check if job with the same name already exists
            for job in jobs:
                if job.get("name") == self._index_cron_name:
                    return  # Already registered, skip

            # Ensure standalone script exists
            self._ensure_index_script()

            # Register cron job (no_agent mode, daily at 03:00)
            import uuid
            now = __import__("datetime").datetime.now().isoformat()
            new_job = {
                "id": uuid.uuid4().hex[:12],
                "name": self._index_cron_name,
                "prompt": None,
                "schedule": {
                    "kind": "cron",
                    "expr": "0 3 * * *",
                    "display": "0 3 * * *",
                },
                "schedule_display": "0 3 * * *",
                "repeat": None,
                "deliver": "local",
                "state": "scheduled",
                "script": self._index_cron_script_name,
                "no_agent": True,
                "created_at": now,
            }
            jobs.append(new_job)
            data["jobs"] = jobs
            data["updated_at"] = now
            cron_file.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            logger.debug("Index sweep cron registered: daily at 03:00")
        except Exception as e:
            logger.debug("Index cron registration failed: %s", e)

    def _remove_index_cron(self):
        """Clean up index sweep cron registration and script (called during shutdown)"""
        try:
            # 1. Remove from jobs.json
            cron_file = self._base / "cron" / "jobs.json"
            if cron_file.exists():
                data = json.loads(cron_file.read_text())
                before = len(data.get("jobs", []))
                data["jobs"] = [
                    j for j in data.get("jobs", [])
                    if j.get("name") != self._index_cron_name
                ]
                after = len(data.get("jobs", []))
                if after < before:
                    data["updated_at"] = __import__("datetime").datetime.now().isoformat()
                    cron_file.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    logger.debug("Removed index sweep cron from jobs.json")

            # 2. Delete standalone script
            script_path = self._base / "scripts" / self._index_cron_script_name
            if script_path.exists():
                script_path.unlink()
                logger.debug("Removed index sweep script: %s", script_path)

        except Exception as e:
            logger.debug("Index cron removal error: %s", e)

    # ── Migration / cleanup ─────────────────────────────────────────

    def _migrate_t_values(self):
        """Migrate old-format t values (day-counter) to Unix timestamps"""
        try:
            activity_path = self._memories_dir / "ACTIVITY.yaml"
            if not activity_path.exists():
                return
            raw = activity_path.read_text(encoding="utf-8")
            data = parse_activity(raw)
            memories = data.get("memories", {})
            changed = False
            for topic, info in memories.items():
                raw_t = info.get("t", 0)
                # Old format: value < 1e9 is a day-counter (e.g. t=7 means 7 days ago)
                if isinstance(raw_t, (int, float)) and 0 < raw_t < 1000000000:
                    from curve_memory.core.activity import format_timestamp
                    info["t"] = format_timestamp()
                    changed = True
            if changed:
                activity_path.write_text(format_activity(data), encoding="utf-8")
                logger.debug("Migrated t values to timestamps")
        except Exception as e:
            logger.debug("Migration error: %s", e)

    def _cleanup_old_cron(self):
        """Clean up old cron jobs and scripts"""
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
        """Lazy archive sweep: scan all memories, archive those exceeding threshold"""
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
                from curve_memory.core.activity import parse_timestamp
                t_days = (now - parse_timestamp(info.get("t", 0))) / 86400
                # Condense to TIER_1 (minimum) before archiving
                degrade_memory(topic, self._memories_dir)
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

**Source:** curve-memory mature promotion
**Solidified at:** {now_str}
**Access count:** {info.get('access_count', 0)}
**Original archive:** archive/mature/{topic}.md
**Note:** This file was auto-generated by the forgetting curve system.

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
            "TIER_4 (≤3d), TIER_3 (≤7d), TIER_2 (≤14d), ARCHIVE (≥30d). "
            "Memory files may contain 'note:' references to detailed notes stored "
            "separately — use curve_memory_read_note to load them on demand."
        ]
        if self._user_profile_raw:
            # Truncate overly long natural language section to save tokens
            raw = self._user_profile_raw[:500]
            if len(self._user_profile_raw) > 500:
                raw += "\n... (truncated, full profile in USER.md)"
            lines.append(f"\n## User Profile\n{raw}")
        return "\n".join(lines)

    # ── Prefetch ────────────────────────────────────────────────────

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Recall relevant memories + user profile before each turn"""
        parts = []

        # Recall memories
        if self._searcher and query.strip():
            try:
                results = self._searcher.search(query, top_k=3)
                if results:
                    blocks = []
                    for topic, score, snippet, r in results:
                        tier = r_to_tier_name(r)
                        block = f"### {topic} ({tier})\n{snippet}"
                        # Check for note references
                        note_refs = self._searcher.get_note_refs(topic)
                        if note_refs:
                            for ref in note_refs:
                                block += f"\n_📝 [note:{ref}]_"
                        blocks.append(block)
                        self._touched_topics.add(topic)
                    parts.append("## Retrieved Memories\n\n" + "\n\n".join(blocks))
            except Exception as e:
                logger.debug("prefetch error: %s", e)

        # User profile (inject when query matches profile keywords)
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
        """Update activity for referenced memories after each turn

        During the day, only update timestamps (_touch_memory), no degradation.
        TIER degradation and semantic condensation are handled by the daily cron.
        """
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
            result = {"profile": dict(self._user_profile)}
            if self._user_profile_raw:
                result["raw"] = self._user_profile_raw[:300]
            return json.dumps(result)

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

        if tool_name == "curve_memory_enrich":
            topic = str(args.get("topic", "")).strip()
            content = str(args.get("content", "")).strip()
            summary = str(args.get("summary", "")).strip() or None
            if not topic or not content:
                return json.dumps({"error": "Both 'topic' and 'content' are required"})
            from curve_memory.enrichment import enrich_memory
            ok = enrich_memory(topic, content, self._memories_dir, summary=summary)
            return json.dumps({"status": "ok" if ok else "skipped", "topic": topic})

        if tool_name == "curve_memory_degrade_now":
            from curve_memory.enrichment import degradation_sweep
            degraded = degradation_sweep(self._memories_dir)
            return json.dumps({"status": "ok", "degraded": len(degraded), "topics": degraded})

        if tool_name == "curve_memory_read_note":
            note_name = str(args.get("note_name", "")).strip()
            if not note_name:
                return json.dumps({"error": "note_name is required"})
            from curve_memory.core.note import read_note
            if not self._notes_dir:
                return json.dumps({"error": "Notes system not initialized"})
            content = read_note(note_name, self._notes_dir)
            if content is None:
                return json.dumps({"error": f"Note '{note_name}' not found"})
            return json.dumps({"note_name": note_name, "content": content})

        raise NotImplementedError(f"Unknown tool: {tool_name}")

    # ── Session end ─────────────────────────────────────────────────

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """Lazy archive sweep at session end"""
        self._archive_sweep()

    # ── Shutdown ────────────────────────────────────────────────────

    def shutdown(self):
        self._save_user_profile()
        # Clean up index sweep cron registration and script
        self._remove_index_cron()
        self._searcher = None
        self._embedder = None
        self._cfg = {}
        logger.debug("CurveMemory shut down")
