#!/usr/bin/env python3
"""
hermes curve-memory — Forgetting Curve Memory System CLI

7 subcommands: search, status, config, check, activate, deactivate, index
"""

import argparse
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

from curve_memory.core.config import load_config, save_config, get_config_schema, schema_values_to_config, format_config
from curve_memory.core.embedding import create_embedding_provider
from curve_memory.core.search import HybridSearch
from curve_memory.core.activity import parse_activity, format_activity, load_activity
from curve_memory.core.tier import forgetting_curve, r_to_tier_name

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────

def _get_hermes_home() -> Path:
    """Get hermes_home path"""
    return Path.home() / ".hermes"


def _get_memories_dir() -> Path:
    return _get_hermes_home() / "memories"


def _get_embedder():
    cfg = load_config(str(_get_hermes_home()))
    return create_embedding_provider(cfg.get("embedding", {}))


def _get_searcher(embedder=None):
    cfg = load_config(str(_get_hermes_home()))
    if embedder is None:
        embedder = _get_embedder()
    return HybridSearch(
        _get_memories_dir(),
        embedder=embedder,
        alpha=cfg.get("search", {}).get("alpha", 0.35),
        beta=cfg.get("search", {}).get("beta", 0.45),
        gamma=cfg.get("search", {}).get("gamma", 0.20),
    )


# ── Commands ─────────────────────────────────────────────────────────

def cmd_search(args):
    """Three-way hybrid search"""
    embedder = _get_embedder()
    searcher = _get_searcher(embedder)
    results = searcher.search(args.query, top_k=args.top_k)

    if args.json:
        print(json.dumps([
            {"topic": t, "score": round(s, 4), "r": round(r, 4),
             "tier": r_to_tier_name(r), "snippet": sn}
            for t, s, sn, r in results
        ], ensure_ascii=False, indent=2))
    else:
        print(f"🔍 Search: '{args.query}'  (deg:{searcher.degrade_level}:{searcher.degrade_info})")
        print()
        if not results:
            print("  (no results)")
        for topic, score, snippet, r in results:
            tier = r_to_tier_name(r)
            print(f"  [{tier}] {topic}  (score={score:.3f}, R={r:.4f})")
            if snippet:
                print(f"    {snippet[:120]}...")
            print()


def cmd_status(args):
    """View system status"""
    memories_dir = _get_memories_dir()
    print("=== Curve Memory Status ===")
    print()

    # Memory count
    active_files = list((memories_dir / "active").glob("*.md"))
    print(f"📁 Active memories: {len(active_files)}")

    # Archive count
    forgotten = list((memories_dir / "archive" / "forgotten").glob("*.md"))
    mature = list((memories_dir / "archive" / "mature").glob("*.md"))
    print(f"📦 Archived (forgotten): {len(forgotten)}")
    print(f"🎓 Archived (mature): {len(mature)}")

    # Knowledge
    knowledge_dir = _get_hermes_home() / "knowledge"
    knowledge = list(knowledge_dir.glob("*.md")) if knowledge_dir.exists() else []
    print(f"📚 Knowledge docs: {len(knowledge)}")

    # TIER distribution
    activity_file = memories_dir / "ACTIVITY.yaml"
    if activity_file.exists():
        raw = activity_file.read_text(encoding="utf-8")
        data = parse_activity(raw)
        memories = data.get("memories", {})
        tier_dist = {}
        now = time.time()
        for topic, info in memories.items():
            from curve_memory.core.activity import parse_timestamp
            t_days = (now - parse_timestamp(info.get("t", 0))) / 86400
            r = forgetting_curve(t_days)
            tier = r_to_tier_name(r)
            tier_dist[tier] = tier_dist.get(tier, 0) + 1

        print()
        print("📊 TIER Distribution:")
        for tier in ["TIER_5 🔥", "TIER_4 📗", "TIER_3 📙", "TIER_2 📕", "TIER_1 📦", "ARCHIVE 🗄️"]:
            count = tier_dist.get(tier, 0)
            bar = "█" * count
            print(f"  {tier:15s}: {count:2d} {bar}")

    # Index status
    embedding_dir = memories_dir / ".embedding_index"
    fts5_path = memories_dir / ".fts5" / "curve_memory_fts5.db"
    print(f"\n🔎 Embedding index: {'✅' if embedding_dir.exists() and any(embedding_dir.iterdir()) else '❌'}")
    print(f"🔎 FTS5 index: {'✅' if fts5_path.exists() else '❌'}")

    # Embedder
    embedder = _get_embedder()
    if embedder:
        print(f"🤖 Embedder: {embedder.name} ✅")
    else:
        print("🤖 Embedder: None (degraded)")


def cmd_config(args):
    """View config / Interactive config"""
    if args.interactive:
        _interactive_config()
        return
    try:
        cfg = load_config(str(_get_hermes_home()))
        print(format_config(cfg))
    except Exception as e:
        print(f"Failed to load config: {e}")


def _interactive_config():
    """Interactive config wizard — writes JSON config file"""
    schema = get_config_schema()
    print("=== Curve Memory Config Wizard ===")
    print("Press Enter to use defaults.\n")
    values = {}
    for field in schema:
        key = field["key"]
        desc = field["description"]
        default = field["default"]
        try:
            val = input(f"  {desc} [{default}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled")
            return
        if val:
            values[key] = val
        else:
            values[key] = default
    cfg = schema_values_to_config(values)
    save_config(cfg, str(_get_hermes_home()))
    print("\n✅ Config saved to curve-memory-config.json")
    print("   View: hermes curve-memory config")
    print("   Restart: hermes gateway restart")


def cmd_check(args):
    """Health check"""
    memories_dir = _get_memories_dir()
    hermes_home = _get_hermes_home()
    print("=== Curve Memory Health Check ===")
    print()

    # 1. ACTIVITY.yaml
    activity_path = memories_dir / "ACTIVITY.yaml"
    print(f"[1/5] ACTIVITY.yaml: {'✅' if activity_path.exists() else '❌'}")
    if activity_path.exists():
        raw = activity_path.read_text(encoding="utf-8")
        if "metadata" in raw and "memories" in raw:
            print("      Format: v3 ✅")
        else:
            print("      Format: unknown ❌")

    # 2. Directory structure
    for d in ["active", "archive/forgotten", "archive/mature"]:
        p = memories_dir / d
        print(f"[2/5] memories/{d}: {'✅' if p.exists() else '❌'}")

    # 3. Embedder connectivity
    embedder = _get_embedder()
    if embedder:
        print(f"[3/5] Embedder: ✅ ({embedder.name}, dim={embedder.dim})")
    else:
        print(f"[3/5] Embedder: ❌ (degraded to BM25 + R(t))")

    # 4. Index integrity
    embedding_dir = memories_dir / ".embedding_index"
    fts5_path = memories_dir / ".fts5" / "curve_memory_fts5.db"
    print(f"[4/5] Embedding index: {'✅' if embedding_dir.exists() and any(embedding_dir.iterdir()) else '❌'}")
    print(f"[4/5] FTS5 index: {'✅' if fts5_path.exists() else '❌'}")

    # 5. Configuration
    config_path = hermes_home / "curve-memory-config.json"
    print(f"[5/5] Config file: {'✅' if config_path.exists() else '❌ (using defaults)'}")


def cmd_activate(args):
    """Activate curve memory system"""
    try:
        subprocess.run(["hermes", "config", "set", "memory.provider", "curve-memory"],
                       capture_output=True, timeout=10)
        print("✅ curve-memory enabled")
        print("   Restart: hermes gateway restart")
    except Exception as e:
        print(f"⚠️  {e}")
        print("   Manual: hermes config set memory.provider curve-memory")


def cmd_deactivate(args):
    """Deactivate curve memory system"""
    try:
        subprocess.run(["hermes", "config", "unset", "memory.provider"],
                       capture_output=True, timeout=10)
        print("✅ curve-memory deactivated (data preserved)")
        print("   Reactivate: hermes curve-memory activate")
    except Exception as e:
        print(f"⚠️  {e}")
        print("   Manual: hermes config unset memory.provider")


def cmd_index(args):
    """Build index (incremental/full)"""
    import sqlite3
    import hashlib

    memories_dir = _get_memories_dir()
    active_dir = memories_dir / "active"
    embedding_dir = memories_dir / ".embedding_index"
    fts5_dir = memories_dir / ".fts5"
    fts5_path = fts5_dir / "curve_memory_fts5.db"
    mtime_cache_path = memories_dir / ".mtime_cache.json"

    # Create directories
    fts5_dir.mkdir(parents=True, exist_ok=True)
    embedding_dir.mkdir(parents=True, exist_ok=True)

    embedder = _get_embedder()
    activity = load_activity(memories_dir)
    memories = activity.get("memories", {}) if activity else {}

    now = time.time()

    # Initialize FTS5
    conn = sqlite3.connect(str(fts5_path))
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
            topic, content, tokenize='unicode61'
        )
    """)
    conn.commit()

    # Load mtime cache
    mtime_cache = {}
    if mtime_cache_path.exists() and not args.rebuild:
        mtime_cache = json.loads(mtime_cache_path.read_text())

    topics = sorted([f.stem for f in active_dir.glob("*.md") if f.is_file()])
    changed = 0
    skipped = 0

    for topic in topics:
        filepath = active_dir / f"{topic}.md"
        if not filepath.exists():
            continue

        current_mtime = str(filepath.stat().st_mtime)

        # Incremental: skip unchanged files
        if not args.rebuild and mtime_cache.get(topic) == current_mtime:
            skipped += 1
            continue

        content = filepath.read_text(encoding="utf-8")

        # Get TIER level
        info = memories.get(topic, {})
        from curve_memory.core.activity import parse_timestamp
        t_days = (now - parse_timestamp(info.get("t", 0))) / 86400
        r = forgetting_curve(t_days)
        tier_level = __import__("curve_memory.core.tier", fromlist=["r_to_tier_level"]).r_to_tier_level(r)

        # Chunk by TIER level
        lines = content.splitlines()
        max_chars = {5: 2000, 4: 1000, 3: 500, 2: 300, 1: 100}.get(tier_level, 100)
        chunks = []
        for i in range(0, len(lines), max(1, max_chars // 80)):
            block = "\n".join(lines[i:i + max(1, max_chars // 80)])
            chunks.append({
                "seq": len(chunks),
                "text": block[:max_chars],
                "tier": tier_level,
            })
        if not chunks:
            chunks.append({"seq": 0, "text": content[:max_chars], "tier": tier_level})

        # Embedding
        if embedder:
            embedding_file = embedding_dir / f"{topic}.jsonl"
            with open(embedding_file, "w", encoding="utf-8") as f:
                for chunk in chunks:
                    text = chunk["text"]
                    if not text.strip():
                        continue
                    try:
                        vector = embedder.embed(text)
                    except Exception as e:
                        print(f"  ⚠️  {topic}/{chunk['chunk']}: embed failed: {e}")
                        continue
                    record = {
                        "topic": topic,
                        "chunk": chunk["chunk"],
                        "text": text,
                        "vector": vector,
                    }
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(f"  📦 {topic}: {len(chunks)} chunks embedded")

        # FTS5
        conn.execute("DELETE FROM memory_fts WHERE topic = ?", (topic,))
        conn.execute("INSERT INTO memory_fts (topic, content) VALUES (?, ?)", (topic, content))
        conn.commit()

        mtime_cache[topic] = current_mtime
        changed += 1

    # Clean up stale topic indices
    active_set = set(topics)
    for fpath in embedding_dir.glob("*.jsonl"):
        if fpath.stem not in active_set:
            fpath.unlink()
    cursor = conn.execute("SELECT DISTINCT topic FROM memory_fts")
    for row in cursor.fetchall():
        if row[0] not in active_set:
            conn.execute("DELETE FROM memory_fts WHERE topic = ?", (row[0],))
    conn.commit()
    conn.close()

    # Clean mtime cache
    stale = [t for t in mtime_cache if t not in active_set]
    for t in stale:
        del mtime_cache[t]

    mtime_cache_path.write_text(json.dumps(mtime_cache, ensure_ascii=False, indent=2))

    print(f"\n📊 Index: {changed} changed, {skipped} skipped" if not args.rebuild else f"\n📊 Rebuilt: {changed} topics")


# ── Notes commands ────────────────────────────────────────────────────


def cmd_notes_list(args):
    """List all notes"""
    from curve_memory.core.note import list_notes
    notes_dir = _get_hermes_home() / "notes"
    notes = list_notes(notes_dir)
    if not notes:
        print("📝 No notes")
        return
    print(f"📝 Notes ({len(notes)}):")
    for n in notes:
        print(f"  • {n}")


def cmd_notes_show(args):
    """View note content"""
    from curve_memory.core.note import read_note
    notes_dir = _get_hermes_home() / "notes"
    content = read_note(args.name, notes_dir)
    if content:
        print(content)
    else:
        print(f"❌ Note '{args.name}' not found")
        print(f"   Location: {notes_dir / f'{args.name}.md'}")


def cmd_notes_delete(args):
    """Delete note"""
    from curve_memory.core.note import delete_note
    notes_dir = _get_hermes_home() / "notes"
    if delete_note(args.name, notes_dir):
        print(f"✅ Note '{args.name}' deleted")
    else:
        print(f"❌ Note '{args.name}' not found")


# ── Semantic degradation ─────────────────────────────────────────────


def cmd_degrade_semantic(args):
    """Semantic degradation: condense memories exceeding TIER targets"""
    from curve_memory.backends.generate import OllamaGenerate
    from curve_memory.core.activity import load_activity
    from curve_memory.core.tier import r_to_tier_level
    from curve_memory.enrichment import _target_size, _r_for_topic
    import time

    hermes_home = _get_hermes_home()
    memories_dir = _get_memories_dir()
    notes_dir = hermes_home / "notes"

    # Load activity data
    activity = load_activity(memories_dir)
    if not activity:
        print("❌ Failed to load ACTIVITY.yaml")
        return

    memories = activity.get("memories", {})
    if not memories:
        print("✅ No active memories")
        return

    now = time.time()
    from curve_memory.core.tier import forgetting_curve, r_to_tier_level
    from curve_memory.enrichment import _target_size, _parse_memory, _build_memory

    # Compute TIER for each memory, check if degradation needed
    pending = []
    for topic, info in memories.items():
        from curve_memory.core.activity import parse_timestamp
        t_days = (now - parse_timestamp(info.get("t", 0))) / 86400
        r = forgetting_curve(t_days)
        tier = r_to_tier_level(r)
        mem_path = memories_dir / "active" / f"{topic}.md"
        if not mem_path.exists():
            continue
        content = mem_path.read_text(encoding="utf-8")
        if len(content) > _target_size(tier):
            pending.append((topic, tier, len(content)))

    if not pending:
        print("✅ All memories within TIER targets, no processing needed")
        return

    print(f"🔍 Found {len(pending)} topics requiring degradation")
    if args.dry_run:
        print("\n=== Preview (dry-run) ===")
        for topic, tier, size in pending:
            target = _target_size(tier)
            print(f"  📄 {topic}: TIER_{tier}, {size} → {target} chars")
        print(f"\n  --max-topics N to limit processing count")
        return

    # Execute
    gen = OllamaGenerate(model="qwen2.5:3b", timeout=90)
    processed = 0
    failed = 0
    skipped = 0

    max_topics = args.max_topics if args.max_topics > 0 else len(pending)
    topics_to_process = pending[:max_topics]

    print(f"\n🔄 Processing {len(topics_to_process)}/{len(pending)} topics...")
    for topic, tier, orig_size in topics_to_process:
        mem_path = memories_dir / "active" / f"{topic}.md"
        if not mem_path.exists():
            print(f"  ⚠️  {topic}: file has been removed, skipping")
            skipped += 1
            continue

        content = mem_path.read_text(encoding="utf-8")
        target = _target_size(tier)

        # Double-check size (file may have been modified since scan)
        if len(content) <= target:
            print(f"  ⏭️  {topic}: already within {target} char limit, skipping")
            skipped += 1
            continue

        print(f"  📄 {topic}: TIER_{tier}, {len(content)} → {target} chars...", end="", flush=True)

        # Parse memory file
        from curve_memory.core.note import extract_note_refs
        from curve_memory.enrichment import _parse_memory, _build_memory
        parsed = _parse_memory(content)
        original_refs = parsed["note_refs"]
        summary = parsed["summary"]
        details = parsed["details"]
        enriched = parsed["enriched"]

        if original_refs:
            # Has notes → notes contain full details, discard Details section
            condensed = _build_memory(
                topic=topic,
                summary=summary,
                details="(details in notes)",
                enriched=enriched,
                note_refs=original_refs,
            )
            mem_path.write_text(condensed, encoding="utf-8")
            print(f" ✅ {len(condensed)} chars (has notes, kept summary+note refs)")
            processed += 1
            continue

        # No notes → only condense **Details** section, keep **Summary** intact
        if not details:
            # No Details and no notes → content is already minimal form
            print(f" ⏭️  No Details and no notes, skipping")
            skipped += 1
            continue

        detail_target = max(100, target - len(summary) - 50)
        if len(details) <= detail_target * 1.2:
            # Details not significantly over target, skip LLM call
            print(f" ⏭️  Details ({len(details)} chars) near target, skipping")
            skipped += 1
            continue

        # Call Ollama to condense Details
        print(f"details {len(details)}→~{detail_target} chars...", end="", flush=True)
        prompt = f"Keep only the key technical facts from these notes (≤{detail_target} chars):\n\n{details}"
        result = gen.generate(prompt, num_predict=min(detail_target * 2, 400))
        if result and result["text"]:
            condensed_details = result["text"].strip()
            if len(condensed_details) > detail_target:
                condensed_details = condensed_details[:detail_target]
            details = condensed_details

        condensed = _build_memory(
            topic=topic,
            summary=summary,
            details=details,
            enriched=enriched,
            note_refs=original_refs,
        )
        mem_path.write_text(condensed, encoding="utf-8")
        print(f" ✅ {len(condensed)} chars (summary kept, details condensed)")
        processed += 1

    print(f"\n📊 Done: {processed} processed, {skipped} skipped, {failed} failed")


# ── Cron setup ────────────────────────────────────────────────────────


def cmd_install_cron(args):
    """Install cron job (3:00 AM semantic degradation)"""
    import subprocess
    cron_line = (
        "0 3 * * * cd ~/.hermes/plugins/curve-memory && "
        "python3 -m curve_memory.cli degrade-semantic "
        ">> ~/.hermes/logs/degrade-cron.log 2>&1"
    )

    # Try system crontab first
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
        existing = result.stdout
        if "degrade-semantic" in existing:
            print("✅  Cron job already exists (degrade-semantic)")
            return
        new_cron = existing.strip() + "\n" + cron_line + "\n"
        proc = subprocess.run(["crontab", "-"], input=new_cron, text=True,
                              capture_output=True, timeout=10)
        if proc.returncode == 0:
            print("✅  Cron job installed: 0 3 * * * degrade-semantic")
            print("   Log: ~/.hermes/logs/degrade-cron.log")
            return
        print(f"⚠️  crontab install failed: {proc.stderr}")
    except FileNotFoundError:
        print("⚠️  system crontab unavailable, trying Hermes cron scheduler...")
    except Exception as e:
        print(f"⚠️  system crontab failed: {e}")

    # Fallback: Hermes cron scheduler via ~/.hermes/cron/jobs.json
    try:
        cron_dir = Path.home() / ".hermes" / "cron"
        cron_dir.mkdir(parents=True, exist_ok=True)
        cron_file = cron_dir / "jobs.json"

        if cron_file.exists():
            data = json.loads(cron_file.read_text())
        else:
            data = {"jobs": [], "updated_at": ""}
        jobs = data.get("jobs", [])

        # Check if already registered
        for job in jobs:
            if job.get("name") == "curve-memory-degrade-semantic":
                print("✅  Hermes cron job already exists (degrade-semantic)")
                return

        # Ensure standalone script exists
        scripts_dir = Path.home() / ".hermes" / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        script_path = scripts_dir / "curve-memory-degrade-semantic.py"
        if not script_path.exists():
            script_content = '''#!/usr/bin/env python3
"""Auto-generated by curve-memory plugin - semantic degradation cron entry point."""
import sys, os
from pathlib import Path
sys.path.insert(0, str(Path.home() / ".hermes" / "plugins" / "curve-memory"))
from curve_memory.cli import cmd_degrade_semantic
class _A:
    dry_run = False
    max_topics = 0
try:
    cmd_degrade_semantic(_A())
except Exception as e:
    print(f"degrade-semantic cron failed: {e}")
    sys.exit(1)
'''
            script_path.write_text(script_content, encoding="utf-8")
            script_path.chmod(0o755)

        import uuid
        now = __import__("datetime").datetime.now().isoformat()
        new_job = {
            "id": uuid.uuid4().hex[:12],
            "name": "curve-memory-degrade-semantic",
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
            "script": "curve-memory-degrade-semantic.py",
            "no_agent": True,
            "created_at": now,
        }
        jobs.append(new_job)
        data["jobs"] = jobs
        data["updated_at"] = now
        cron_file.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"✅  Hermes cron job installed: 0 3 * * * degrade-semantic")
        print(f"   Script: {script_path}")
    except Exception as e:
        print(f"❌  Install failed: {e}")
        print("   Please add crontab entry manually:")
        print(f"   {cron_line}")


# ── Registration ─────────────────────────────────────────────────────

def register_cli(subparser) -> None:
    """Called by Hermes memory provider CLI discovery."""
    subs = subparser.add_subparsers(dest="curve_memory_command")
    register_subcommands(subs)


def register_subcommands(sub):
    """Register all subcommands (7)"""
    p_search = sub.add_parser("search", help="Three-way hybrid search")
    p_search.add_argument("query", help="Search keywords")
    p_search.add_argument("--top-k", type=int, default=5, help="Number of results")
    p_search.add_argument("--json", action="store_true", help="JSON output")
    p_search.set_defaults(func=cmd_search)

    p_status = sub.add_parser("status", help="View system status")
    p_status.set_defaults(func=cmd_status)

    p_config = sub.add_parser("config", help="View current config or interactive config")
    p_config.add_argument("-i", "--interactive", action="store_true", help="Interactive config wizard")
    p_config.set_defaults(func=cmd_config)

    p_check = sub.add_parser("check", help="Health check")
    p_check.set_defaults(func=cmd_check)

    p_activate = sub.add_parser("activate", help="Reactivate the curve memory system")
    p_activate.set_defaults(func=cmd_activate)

    p_deactivate = sub.add_parser("deactivate", help="Deactivate curve memory system (preserve data)")
    p_deactivate.set_defaults(func=cmd_deactivate)

    p_index = sub.add_parser("index", help="Build index (incremental/full)")
    p_index.add_argument("--rebuild", action="store_true", help="Full rebuild")
    p_index.set_defaults(func=cmd_index)

    # ── Notes subcommands ──────────────────────────────────────────
    p_notes_list = sub.add_parser("notes-list", help="List all notes")
    p_notes_list.set_defaults(func=cmd_notes_list)

    p_notes_show = sub.add_parser("notes-show", help="View note content")
    p_notes_show.add_argument("name", help="Note name (without .md)")
    p_notes_show.set_defaults(func=cmd_notes_show)

    p_notes_delete = sub.add_parser("notes-delete", help="Delete note")
    p_notes_delete.add_argument("name", help="Note name (without .md)")
    p_notes_delete.set_defaults(func=cmd_notes_delete)

    # ── Semantic Degradation ───────────────────────────────────────
    p_degrade = sub.add_parser("degrade-semantic", help="Semantic degradation (condense memories exceeding TIER targets)")
    p_degrade.add_argument("--dry-run", action="store_true", help="Preview only, no modifications")
    p_degrade.add_argument("--max-topics", type=int, default=0, help="Limit processing count (default: all)")
    p_degrade.set_defaults(func=cmd_degrade_semantic)

    # ── Cron setup ─────────────────────────────────────────────────
    p_cron = sub.add_parser("install-cron", help="Install cron job (3am semantic degradation)")
    p_cron.set_defaults(func=cmd_install_cron)


def main():
    parser = argparse.ArgumentParser(description="Curve Memory System CLI")
    sub = parser.add_subparsers(dest="command")
    register_subcommands(sub)
    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
