#!/usr/bin/env python3
"""
curve-memory-cli — 遗忘曲线记忆系统 CLI

用法：
  curve-memory-cli search <query>        三路检索
  curve-memory-cli index --rebuild       全量重建索引
  curve-memory-cli index --incremental   增量更新索引
  curve-memory-cli status                状态查看
  curve-memory-cli touch <topic>         置 t=0
  curve-memory-cli daily-tick            手动触发衰减
  curve-memory-cli forget <topic>        手动归档
  curve-memory-cli mature <topic>        手动标记成熟
  curve-memory-cli check                 健康检查
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 插件路径兼容
_PLUGIN_PARENT = Path(__file__).resolve().parent.parent  # plugins/curve-memory/
if str(_PLUGIN_PARENT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_PARENT))
_PLUGIN_CORE = Path.home() / ".hermes" / "plugins" / "curve-memory"
if _PLUGIN_CORE.exists() and str(_PLUGIN_CORE) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_CORE))

try:
    from tier import forgetting_curve, r_to_tier_name
    from activity import parse_activity, format_activity
except ModuleNotFoundError:
    from curve_memory.core.tier import forgetting_curve, r_to_tier_name
    from curve_memory.core.activity import parse_activity, format_activity

MEMORIES_DIR = Path.home() / ".hermes" / "memories"


def cmd_search(args):
    from search import HybridSearch
    from tier import r_to_tier_name

    embedder = None
    try:
        from embedding_provider import create_embedding_provider
        embedder = create_embedding_provider()
    except Exception:
        pass

    searcher = HybridSearch(MEMORIES_DIR, embedder=embedder)
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


def cmd_index(args):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "indexer", os.path.join(os.path.dirname(__file__), "curve-memory-indexer.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # 直接调用 indexer 主函数
    sys.argv = ["curve-memory-indexer"]
    if args.rebuild:
        sys.argv.append("--rebuild")
    else:
        sys.argv.append("--incremental")
    indexer_main()


def cmd_status(args):
    print("=== Curve Memory Status ===")
    print()

    # 记忆数量
    active_files = list((MEMORIES_DIR / "active").glob("*.md"))
    print(f"📁 Active memories: {len(active_files)}")

    # 归档数量
    forgotten = list((MEMORIES_DIR / "archive" / "forgotten").glob("*.md"))
    mature = list((MEMORIES_DIR / "archive" / "mature").glob("*.md"))
    print(f"📦 Archived (forgotten): {len(forgotten)}")
    print(f"🎓 Archived (mature): {len(mature)}")

    # Knowledge
    knowledge = list((Path.home() / ".hermes" / "knowledge").glob("*.md"))
    print(f"📚 Knowledge docs: {len(knowledge)}")

    # TIER 分布

    activity_file = MEMORIES_DIR / "ACTIVITY.yaml"
    if activity_file.exists():
        raw = activity_file.read_text(encoding="utf-8")
        data = parse_activity(raw)
        memories = data.get("memories", {})
        tier_dist = {}
        for topic, info in memories.items():
            t = info.get("t", 0)
            r = forgetting_curve(t)
            tier = r_to_tier_name(r)
            tier_dist[tier] = tier_dist.get(tier, 0) + 1

        print()
        print("📊 TIER Distribution:")
        for tier in ["TIER_5 🔥", "TIER_4 📗", "TIER_3 📙", "TIER_2 📕", "TIER_1 📦", "ARCHIVE 🗄️"]:
            count = tier_dist.get(tier, 0)
            bar = "█" * count
            print(f"  {tier:15s}: {count:2d} {bar}")

    # 索引状态
    embedding_dir = MEMORIES_DIR / ".embedding_index"
    fts5_path = MEMORIES_DIR / ".fts5" / "curve_memory_fts5.db"
    print(f"\n🔎 Embedding index: {'✅' if embedding_dir.exists() and any(embedding_dir.iterdir()) else '❌'}")
    print(f"🔎 FTS5 index: {'✅' if fts5_path.exists() else '❌'}")

    # Embedder
    try:
        from embedding_provider import create_embedding_provider
        emb = create_embedding_provider()
        print(f"🤖 Embedder: {emb.name if emb else 'None (degraded)'}")
    except Exception:
        print("🤖 Embedder: None (degraded)")


def cmd_touch(args):
    from activity import parse_activity, format_activity
    raw = (MEMORIES_DIR / "ACTIVITY.yaml").read_text(encoding="utf-8")
    data = parse_activity(raw)
    memories = data.get("memories", {})

    if args.topic in memories:
        memories[args.topic]["t"] = 0
        memories[args.topic]["access_count"] = memories[args.topic].get("access_count", 0) + 1
        (MEMORIES_DIR / "ACTIVITY.yaml").write_text(format_activity(data), encoding="utf-8")
        print(f"✅ {args.topic}: t=0, access_count={memories[args.topic]['access_count']}")
    else:
        print(f"❌ Topic '{args.topic}' not found in ACTIVITY.yaml")


def cmd_daily_tick(args):
    import importlib.util, sys
    spec = importlib.util.spec_from_file_location(
        "forgetting", os.path.join(os.path.dirname(__file__), "curve-memory-forgetting.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.main()


def cmd_forget(args):
    from activity import parse_activity, format_activity
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "forgetting", os.path.join(os.path.dirname(__file__), "curve-memory-forgetting.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    forget_archive = mod.forget_archive
    from tier import forgetting_curve
    raw = (MEMORIES_DIR / "ACTIVITY.yaml").read_text(encoding="utf-8")
    data = parse_activity(raw)

    if args.topic in data.get("memories", {}):
        info = data["memories"][args.topic]
        t = info.get("t", 0)
        r = forgetting_curve(t)
        forget_archive(args.topic, t, r, data)
        (MEMORIES_DIR / "ACTIVITY.yaml").write_text(format_activity(data), encoding="utf-8")
        print(f"✅ {args.topic}: forgotten archived")
    else:
        print(f"❌ Topic '{args.topic}' not found")


def cmd_mature(args):
    from activity import parse_activity, format_activity
    raw = (MEMORIES_DIR / "ACTIVITY.yaml").read_text(encoding="utf-8")
    data = parse_activity(raw)

    if args.topic in data.get("memories", {}):
        data["memories"][args.topic]["mature"] = True
        (MEMORIES_DIR / "ACTIVITY.yaml").write_text(format_activity(data), encoding="utf-8")
        print(f"✅ {args.topic}: marked as mature")
    else:
        print(f"❌ Topic '{args.topic}' not found")


def cmd_check(args):
    """健康检查"""
    print("=== Curve Memory Health Check ===")
    print()

    # 1. ACTIVITY.yaml
    activity_path = MEMORIES_DIR / "ACTIVITY.yaml"
    print(f"[1/6] ACTIVITY.yaml: {'✅' if activity_path.exists() else '❌'}")
    if activity_path.exists():
        raw = activity_path.read_text(encoding="utf-8")
        if "metadata" in raw and "memories" in raw:
            print("      Format: v3 ✅")
        else:
            print("      Format: unknown ❌")

    # 2. 目录结构
    for d in ["active", "archive/forgotten", "archive/mature"]:
        p = MEMORIES_DIR / d
        print(f"[2/6] memories/{d}: {'✅' if p.exists() else '❌'}")

    knowledge_dir = Path.home() / ".hermes" / "knowledge"
    print(f"[2/6] knowledge/: {'✅' if knowledge_dir.exists() else '❌'}")

    # 3. 脚本
    for script in ["tier.py", "activity.py", "chunker.py",
                    "embedding_provider.py", "search.py", "curve-memory-indexer.py",
                    "curve-memory-forgetting.py", "curve-memory-cli.py"]:
        p = Path(__file__).parent / script
        print(f"[3/6] scripts/{script}: {'✅' if p.exists() else '❌'}")

    # 4. 索引
    embedding_dir = MEMORIES_DIR / ".embedding_index"
    fts5_path = MEMORIES_DIR / ".fts5" / "curve_memory_fts5.db"
    print(f"[4/6] Embedding index: {'✅' if embedding_dir.exists() else '❌'}")
    print(f"[4/6] FTS5 index: {'✅' if fts5_path.exists() else '❌'}")

    # 5. Embedder
    try:
        from embedding_provider import create_embedding_provider
        emb = create_embedding_provider()
        if emb:
            print(f"[5/6] Embedder: ✅ ({emb.name}, dim={emb.dim})")
        else:
            print(f"[5/6] Embedder: ❌ (degraded to BM25 + R(t))")
    except Exception as e:
        print(f"[5/6] Embedder: ❌ ({e}, degraded)")

    # 6. Cron
    print(f"[6/6] Cron: check with 'hermes cron list'")


def cmd_setup(args):
    """创建 cron 脚本软链接和定时任务"""
    import os
    import json
    scripts_dir = Path.home() / ".hermes" / "scripts"
    plugin_core = Path.home() / ".hermes" / "plugins" / "curve-memory" / "curve_memory" / "core"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    # 1. 软链接
    links = [
        ("curve-memory-forgetting.py", "forgetting.py"),
        ("curve-memory-indexer.py", "indexer.py"),
    ]
    for link_name, target in links:
        link_path = scripts_dir / link_name
        target_path = plugin_core / target
        if link_path.exists() or link_path.is_symlink():
            link_path.unlink()
        os.symlink(str(target_path), str(link_path))
        print(f"  ✅ Symlink: {link_name}")

    # 2. 恢复 cron 任务
    cron_file = Path.home() / ".hermes" / "cron" / "jobs.json"
    if cron_file.exists():
        data = json.loads(cron_file.read_text())
        existing_names = {j.get("name") for j in data.get("jobs", [])}

        new_jobs = []
        if "snowlyn-memory-decay" not in existing_names:
            new_jobs.append({
                "name": "snowlyn-memory-decay",
                "script": "curve-memory-forgetting.py",
                "no_agent": True,
            })
        if "snowlyn-memory-index" not in existing_names:
            new_jobs.append({
                "name": "snowlyn-memory-index",
                "script": "curve-memory-indexer.py",
                "no_agent": True,
            })

        if new_jobs:
            print(f"  ℹ️  Cron jobs not restored (use 'hermes cron' to add manually)")
            print(f"     Run: hermes cron create --script curve-memory-forgetting.py --schedule '0 3 * * *'")
            print(f"     Run: hermes cron create --script curve-memory-indexer.py --schedule '45 3 * * *'")

    print(f"Setup complete. Cron scripts at {scripts_dir}")


def main():
    parser = argparse.ArgumentParser(description="Curve Memory System CLI")
    sub = parser.add_subparsers(dest="command")

    # search
    p_search = sub.add_parser("search", help="三路混合检索")
    p_search.add_argument("query", help="检索关键词")
    p_search.add_argument("--top-k", type=int, default=5, help="返回条数")
    p_search.add_argument("--json", action="store_true", help="JSON 输出")
    p_search.set_defaults(func=cmd_search)

    # index
    p_index = sub.add_parser("index", help="构建索引")
    p_index.add_argument("--rebuild", action="store_true", help="全量重建")
    p_index.add_argument("--incremental", action="store_true", help="增量更新")
    p_index.set_defaults(func=cmd_index)

    # status
    p_status = sub.add_parser("status", help="状态查看")
    p_status.set_defaults(func=cmd_status)

    # touch
    p_touch = sub.add_parser("touch", help="置 t=0")
    p_touch.add_argument("topic", help="记忆主题")
    p_touch.set_defaults(func=cmd_touch)

    # daily-tick
    p_tick = sub.add_parser("daily-tick", help="手动触发每日衰减")
    p_tick.set_defaults(func=cmd_daily_tick)

    # forget
    p_forget = sub.add_parser("forget", help="手动归档")
    p_forget.add_argument("topic", help="记忆主题")
    p_forget.set_defaults(func=cmd_forget)

    # mature
    p_mature = sub.add_parser("mature", help="手动标记成熟")
    p_mature.add_argument("topic", help="记忆主题")
    p_mature.set_defaults(func=cmd_mature)

    # check
    p_check = sub.add_parser("check", help="健康检查")
    p_check.set_defaults(func=cmd_check)

    # setup
    p_setup = sub.add_parser("setup", help="创建 cron 脚本软链接")
    p_setup.set_defaults(func=cmd_setup)

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
