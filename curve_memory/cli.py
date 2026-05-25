#!/usr/bin/env python3
"""
hermes curve-memory — 遗忘曲线记忆系统 CLI

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
    """获取 hermes_home 路径"""
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
    """三路混合检索"""
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
    """系统状态查看"""
    memories_dir = _get_memories_dir()
    print("=== Curve Memory Status ===")
    print()

    # 记忆数量
    active_files = list((memories_dir / "active").glob("*.md"))
    print(f"📁 Active memories: {len(active_files)}")

    # 归档数量
    forgotten = list((memories_dir / "archive" / "forgotten").glob("*.md"))
    mature = list((memories_dir / "archive" / "mature").glob("*.md"))
    print(f"📦 Archived (forgotten): {len(forgotten)}")
    print(f"🎓 Archived (mature): {len(mature)}")

    # Knowledge
    knowledge_dir = _get_hermes_home() / "knowledge"
    knowledge = list(knowledge_dir.glob("*.md")) if knowledge_dir.exists() else []
    print(f"📚 Knowledge docs: {len(knowledge)}")

    # TIER 分布
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

    # 索引状态
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
    """配置查看 / 交互式配置"""
    if args.interactive:
        _interactive_config()
        return
    try:
        cfg = load_config(str(_get_hermes_home()))
        print(format_config(cfg))
    except Exception as e:
        print(f"加载配置失败: {e}")


def _interactive_config():
    """交互式配置向导 — 写入 JSON 配置文件"""
    schema = get_config_schema()
    print("=== Curve Memory 配置向导 ===")
    print("直接回车使用默认值。\n")
    values = {}
    for field in schema:
        key = field["key"]
        desc = field["description"]
        default = field["default"]
        try:
            val = input(f"  {desc} [{default}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消")
            return
        if val:
            values[key] = val
        else:
            values[key] = default
    cfg = schema_values_to_config(values)
    save_config(cfg, str(_get_hermes_home()))
    print("\n✅ 配置已保存到 curve-memory-config.json")
    print("   查看: hermes curve-memory config")
    print("   重启: hermes gateway restart")


def cmd_check(args):
    """健康检查"""
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

    # 2. 目录结构
    for d in ["active", "archive/forgotten", "archive/mature"]:
        p = memories_dir / d
        print(f"[2/5] memories/{d}: {'✅' if p.exists() else '❌'}")

    # 3. Embedder 连通性
    embedder = _get_embedder()
    if embedder:
        print(f"[3/5] Embedder: ✅ ({embedder.name}, dim={embedder.dim})")
    else:
        print(f"[3/5] Embedder: ❌ (degraded to BM25 + R(t))")

    # 4. 索引完整性
    embedding_dir = memories_dir / ".embedding_index"
    fts5_path = memories_dir / ".fts5" / "curve_memory_fts5.db"
    print(f"[4/5] Embedding index: {'✅' if embedding_dir.exists() and any(embedding_dir.iterdir()) else '❌'}")
    print(f"[4/5] FTS5 index: {'✅' if fts5_path.exists() else '❌'}")

    # 5. 配置
    config_path = hermes_home / "curve-memory-config.json"
    print(f"[5/5] Config file: {'✅' if config_path.exists() else '❌ (using defaults)'}")


def cmd_activate(args):
    """激活曲线记忆系统"""
    try:
        subprocess.run(["hermes", "config", "set", "memory.provider", "curve-memory"],
                       capture_output=True, timeout=10)
        print("✅ curve-memory 已启用")
        print("   重启: hermes gateway restart")
    except Exception as e:
        print(f"⚠️  {e}")
        print("   手动: hermes config set memory.provider curve-memory")


def cmd_deactivate(args):
    """停用曲线记忆系统"""
    try:
        subprocess.run(["hermes", "config", "unset", "memory.provider"],
                       capture_output=True, timeout=10)
        print("✅ curve-memory 已停用（数据已保留）")
        print("   启用: hermes curve-memory activate")
    except Exception as e:
        print(f"⚠️  {e}")
        print("   手动: hermes config unset memory.provider")


def cmd_index(args):
    """构建索引（增量/全量）"""
    import sqlite3
    import hashlib

    memories_dir = _get_memories_dir()
    active_dir = memories_dir / "active"
    embedding_dir = memories_dir / ".embedding_index"
    fts5_dir = memories_dir / ".fts5"
    fts5_path = fts5_dir / "curve_memory_fts5.db"
    mtime_cache_path = memories_dir / ".mtime_cache.json"

    # 创建目录
    fts5_dir.mkdir(parents=True, exist_ok=True)
    embedding_dir.mkdir(parents=True, exist_ok=True)

    embedder = _get_embedder()
    activity = load_activity(memories_dir)
    memories = activity.get("memories", {}) if activity else {}

    now = time.time()

    # 初始化 FTS5
    conn = sqlite3.connect(str(fts5_path))
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
            topic, content, tokenize='unicode61'
        )
    """)
    conn.commit()

    # 加载 mtime 缓存
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

        # 增量模式：跳过未变更的文件
        if not args.rebuild and mtime_cache.get(topic) == current_mtime:
            skipped += 1
            continue

        content = filepath.read_text(encoding="utf-8")

        # 获取 TIER 级别
        info = memories.get(topic, {})
        from curve_memory.core.activity import parse_timestamp
        t_days = (now - parse_timestamp(info.get("t", 0))) / 86400
        r = forgetting_curve(t_days)
        tier_level = __import__("curve_memory.core.tier", fromlist=["r_to_tier_level"]).r_to_tier_level(r)

        # 按 TIER 级别分块
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

    # 清理不存在的 topic 索引
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

    # 清理 mtime cache
    stale = [t for t in mtime_cache if t not in active_set]
    for t in stale:
        del mtime_cache[t]

    mtime_cache_path.write_text(json.dumps(mtime_cache, ensure_ascii=False, indent=2))

    print(f"\n📊 Index: {changed} changed, {skipped} skipped" if not args.rebuild else f"\n📊 Rebuilt: {changed} topics")


# ── Notes commands ────────────────────────────────────────────────────


def cmd_notes_list(args):
    """列出所有笔记"""
    from curve_memory.core.note import list_notes
    notes_dir = _get_hermes_home() / "notes"
    notes = list_notes(notes_dir)
    if not notes:
        print("📝 暂无笔记")
        return
    print(f"📝 笔记 ({len(notes)}):")
    for n in notes:
        print(f"  • {n}")


def cmd_notes_show(args):
    """查看笔记内容"""
    from curve_memory.core.note import read_note
    notes_dir = _get_hermes_home() / "notes"
    content = read_note(args.name, notes_dir)
    if content:
        print(content)
    else:
        print(f"❌ 笔记 '{args.name}' 未找到")
        print(f"   存放位置: {notes_dir / f'{args.name}.md'}")


def cmd_notes_delete(args):
    """删除笔记"""
    from curve_memory.core.note import delete_note
    notes_dir = _get_hermes_home() / "notes"
    if delete_note(args.name, notes_dir):
        print(f"✅ 笔记 '{args.name}' 已删除")
    else:
        print(f"❌ 笔记 '{args.name}' 未找到")


# ── Semantic degradation ─────────────────────────────────────────────


def cmd_degrade_semantic(args):
    """语义降级：处理所有 pending_summary 主题"""
    from curve_memory.backends.generate import OllamaGenerate
    from curve_memory.core.activity import load_activity
    from curve_memory.core.tier import r_to_tier_level
    from curve_memory.enrichment import _target_size, _r_for_topic
    import time

    hermes_home = _get_hermes_home()
    memories_dir = _get_memories_dir()
    notes_dir = hermes_home / "notes"

    # 加载活动数据
    activity = load_activity(memories_dir)
    if not activity:
        print("❌ 无法加载 ACTIVITY.yaml")
        return

    memories = activity.get("memories", {})
    if not memories:
        print("✅ 没有活跃记忆")
        return

    now = time.time()
    from curve_memory.core.tier import forgetting_curve, r_to_tier_level
    from curve_memory.enrichment import _target_size, _parse_memory, _build_memory

    # 为每个记忆计算 TIER，检查是否需要降级
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
        print("✅ 所有记忆大小符合 TIER 目标，无需处理")
        return

    print(f"🔍 发现 {len(pending)} 个需要降级的主题")
    if args.dry_run:
        print("\n=== 预览（dry-run）===")
        for topic, tier, size in pending:
            target = _target_size(tier)
            print(f"  📄 {topic}: TIER_{tier}, {size} → {target} chars")
        print(f"\n  --max-topics N 限制处理数量")
        return

    # 实际运行
    gen = OllamaGenerate(model="qwen2.5:3b", timeout=90)
    processed = 0
    failed = 0
    skipped = 0

    max_topics = args.max_topics if args.max_topics > 0 else len(pending)
    topics_to_process = pending[:max_topics]

    print(f"\n🔄 开始处理 {len(topics_to_process)}/{len(pending)} 个主题...")
    for topic, tier, orig_size in topics_to_process:
        mem_path = memories_dir / "active" / f"{topic}.md"
        if not mem_path.exists():
            print(f"  ⚠️  {topic}: 文件已被移除，跳过")
            skipped += 1
            continue

        content = mem_path.read_text(encoding="utf-8")
        target = _target_size(tier)

        # 再次确认大小（文件可能在扫描后被修改）
        if len(content) <= target:
            print(f"  ⏭️  {topic}: 已在目标 {target} 字符内，跳过")
            skipped += 1
            continue

        print(f"  📄 {topic}: TIER_{tier}, {len(content)} → {target} chars...", end="", flush=True)

        # 解析记忆文件
        from curve_memory.core.note import extract_note_refs
        from curve_memory.enrichment import _parse_memory, _build_memory
        parsed = _parse_memory(content)
        original_refs = parsed["note_refs"]
        summary = parsed["summary"]
        details = parsed["details"]
        enriched = parsed["enriched"]

        if original_refs:
            # 有笔记 → 笔记包含全部细节，直接丢弃 Details 部分
            condensed = _build_memory(
                topic=topic,
                summary=summary,
                details="(details in notes)",
                enriched=enriched,
                note_refs=original_refs,
            )
            mem_path.write_text(condensed, encoding="utf-8")
            print(f" ✅ {len(condensed)} chars (有笔记，保留摘要+笔记引用)")
            processed += 1
            continue

        # 无笔记 → 仅提炼 **Details** 部分，**Summary** 不动
        if not details:
            # 没有 Details 也没有笔记 → 内容已经是超简形式
            print(f" ⏭️  无 Details 和笔记，跳过")
            skipped += 1
            continue

        detail_target = max(100, target - len(summary) - 50)
        if len(details) <= detail_target * 1.2:
            # Details 没有显著超长，不调模型
            print(f" ⏭️  Details ({len(details)} chars) 接近目标，跳过")
            skipped += 1
            continue

        # 调 Ollama 提炼 Details
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
        print(f" ✅ {len(condensed)} chars (保留摘要，详情已提炼)")
        processed += 1

    print(f"\n📊 完成: {processed} 已处理, {skipped} 跳过, {failed} 失败")


# ── Cron setup ────────────────────────────────────────────────────────


def cmd_install_cron(args):
    """安装 cron job（凌晨 3:00 语义降级）"""
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
            print("   日志: ~/.hermes/logs/degrade-cron.log")
            return
        print(f"⚠️  crontab install failed: {proc.stderr}")
    except FileNotFoundError:
        print("⚠️  system crontab 不可用，尝试 Hermes cron scheduler...")
    except Exception as e:
        print(f"⚠️  system crontab 失败: {e}")

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
        print(f"   脚本: {script_path}")
    except Exception as e:
        print(f"❌  安装失败: {e}")
        print("   请手动添加 crontab 条目:")
        print(f"   {cron_line}")


# ── Registration ─────────────────────────────────────────────────────

def register_cli(subparser) -> None:
    """Called by Hermes memory provider CLI discovery."""
    subs = subparser.add_subparsers(dest="curve_memory_command")
    register_subcommands(subs)


def register_subcommands(sub):
    """注册所有子命令（7个）"""
    p_search = sub.add_parser("search", help="三路混合检索")
    p_search.add_argument("query", help="检索关键词")
    p_search.add_argument("--top-k", type=int, default=5, help="返回条数")
    p_search.add_argument("--json", action="store_true", help="JSON 输出")
    p_search.set_defaults(func=cmd_search)

    p_status = sub.add_parser("status", help="系统状态查看")
    p_status.set_defaults(func=cmd_status)

    p_config = sub.add_parser("config", help="查看当前配置或交互式配置")
    p_config.add_argument("-i", "--interactive", action="store_true", help="交互式配置向导")
    p_config.set_defaults(func=cmd_config)

    p_check = sub.add_parser("check", help="健康检查")
    p_check.set_defaults(func=cmd_check)

    p_activate = sub.add_parser("activate", help="重新激活曲线记忆系统")
    p_activate.set_defaults(func=cmd_activate)

    p_deactivate = sub.add_parser("deactivate", help="停用曲线记忆系统（保留数据）")
    p_deactivate.set_defaults(func=cmd_deactivate)

    p_index = sub.add_parser("index", help="构建索引（增量/全量）")
    p_index.add_argument("--rebuild", action="store_true", help="全量重建")
    p_index.set_defaults(func=cmd_index)

    # ── Notes subcommands ──────────────────────────────────────────
    p_notes_list = sub.add_parser("notes-list", help="列出所有笔记")
    p_notes_list.set_defaults(func=cmd_notes_list)

    p_notes_show = sub.add_parser("notes-show", help="查看笔记内容")
    p_notes_show.add_argument("name", help="笔记名称（不含 .md）")
    p_notes_show.set_defaults(func=cmd_notes_show)

    p_notes_delete = sub.add_parser("notes-delete", help="删除笔记")
    p_notes_delete.add_argument("name", help="笔记名称（不含 .md）")
    p_notes_delete.set_defaults(func=cmd_notes_delete)

    # ── Semantic Degradation ───────────────────────────────────────
    p_degrade = sub.add_parser("degrade-semantic", help="语义降级（处理 pending_summary 主题）")
    p_degrade.add_argument("--dry-run", action="store_true", help="仅预览，不做实际修改")
    p_degrade.add_argument("--max-topics", type=int, default=0, help="限制处理数量（默认全部）")
    p_degrade.set_defaults(func=cmd_degrade_semantic)

    # ── Cron setup ─────────────────────────────────────────────────
    p_cron = sub.add_parser("install-cron", help="安装 cron job（凌晨3点语义降级）")
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
