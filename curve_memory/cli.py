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
            raw_t = info.get("t", 0)
            if isinstance(raw_t, (int, float)) and raw_t > 1000000000000:
                t_days = (now - raw_t) / 86400
            else:
                t_days = raw_t
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
        raw_t = info.get("t", 0)
        if isinstance(raw_t, (int, float)) and raw_t > 1000000000000:
            t_days = (now - raw_t) / 86400
        else:
            t_days = raw_t
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
