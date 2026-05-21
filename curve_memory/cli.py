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
    """初始化：复制 cron 脚本、注册定时任务、检查目录结构"""
    import os, json, shutil
    from pathlib import Path

    memories_dir = Path.home() / ".hermes" / "memories"
    scripts_dir = Path.home() / ".hermes" / "scripts"
    plugin_core = Path.home() / ".hermes" / "plugins" / "curve-memory" / "curve_memory" / "core"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    # 0. 创建必要的目录
    for d in ["active", "archive/forgotten", "archive/mature", ".embedding_index", ".fts5"]:
        (memories_dir / d).mkdir(parents=True, exist_ok=True)
    Path.home() / ".hermes" / "knowledge"
    print("  ✅ Directory structure ready")

    # 1. 复制 cron 脚本
    for name, target in [("curve-memory-forgetting.py", "forgetting.py"),
                          ("curve-memory-indexer.py", "indexer.py")]:
        dest = scripts_dir / name
        src = plugin_core / target
        if dest.exists() or dest.is_symlink():
            dest.unlink()
        shutil.copy2(str(src), str(dest))
        print(f"  ✅ Copied: {name}")

    # 2. 注册 cron 任务
    cron_file = Path.home() / ".hermes" / "cron" / "jobs.json"
    if cron_file.exists():
        data = json.loads(cron_file.read_text())
        existing_names = {j.get("name") for j in data.get("jobs", [])}
        registered = 0
        for jname, script, sched in [
            ("snowlyn-memory-decay", "curve-memory-forgetting.py", "0 3 * * *"),
            ("snowlyn-memory-index", "curve-memory-indexer.py", "45 3 * * *"),
        ]:
            if jname not in existing_names:
                data["jobs"].append({
                    "id": jname,
                    "name": jname,
                    "script": script,
                    "no_agent": True,
                    "schedule": {"kind": "cron", "expr": sched, "display": sched},
                    "enabled": True,
                    "state": "scheduled",
                    "repeat": {"times": None, "completed": 0},
                    "deliver": "local",
                })
                registered += 1
        if registered:
            cron_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            print(f"  ✅ Registered {registered} cron job(s)")
        else:
            print("  ✅ Cron jobs already registered")
    else:
        print("  ⚠️  Cron system not ready")

    # 3. 检查嵌入模型
    try:
        import subprocess
        r = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=10)
        if "qwen3-embedding" in r.stdout:
            print("  ✅ Embedding model: qwen3-embedding:8b ready")
        else:
            print("  ⚠️  Run: ollama pull qwen3-embedding:8b")
    except Exception:
        print("  ⚠️  Ollama not detected")

    print("Setup complete.")


def cmd_uninstall(args):
    """卸载：清除 cron 脚本、cron 任务、配置、选项性清除数据"""
    import shutil, json, subprocess
    scripts_dir = Path.home() / ".hermes" / "scripts"
    memories_dir = Path.home() / ".hermes" / "memories"
    knowledge_dir = Path.home() / ".hermes" / "knowledge"

    # 确认
    if not args.yes:
        try:
            confirm = input("⚠️  确定要卸载 curve-memory 吗？数据将保留。[y/N] ")
            if confirm.lower() != 'y':
                print("已取消")
                return
        except (EOFError, KeyboardInterrupt):
            print("\n已取消")
            return

    if args.all:
        print("⚠️  这将删除所有记忆数据，包括 knowledge/ 中的永久知识！")
        try:
            confirm = input("再次确认：输入 'delete all' 继续：")
            if confirm != 'delete all':
                print("已取消")
                return
        except (EOFError, KeyboardInterrupt):
            print("\n已取消")
            return

    print("=== Uninstalling curve-memory ===")

    # 1. 删除 cron 脚本
    for name in ["curve-memory-forgetting.py", "curve-memory-indexer.py"]:
        p = scripts_dir / name
        if p.exists():
            p.unlink()
            print(f"  ✅ Removed: {name}")

    # 2. 删除 cron 任务
    cron_file = Path.home() / ".hermes" / "cron" / "jobs.json"
    if cron_file.exists():
        data = json.loads(cron_file.read_text())
        before = len(data.get("jobs", []))
        data["jobs"] = [
            j for j in data.get("jobs", [])
            if "snowlyn-memory-decay" not in j.get("name", "")
            and "snowlyn-memory-index" not in j.get("name", "")
        ]
        after = len(data.get("jobs", []))
        cron_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        print(f"  ✅ Removed {before - after} cron job(s)")

    # 3. 清理 memory.plugin 配置
    try:
        subprocess.run(["hermes", "config", "unset", "memory.plugin"],
                       capture_output=True, timeout=10)
        print("  ✅ Cleared memory.plugin config")
    except Exception:
        print("  ⚠️  Run manually: hermes config unset memory.plugin")

    # 4. 清除数据（仅 --all 时）
    if args.all:
        for d in [memories_dir / ".embedding_index",
                   memories_dir / ".fts5",
                   memories_dir / "archive" / "forgotten",
                   memories_dir / "archive" / "mature",
                   knowledge_dir]:
            if d.exists():
                shutil.rmtree(d)
                print(f"  ✅ Removed: {d.relative_to(Path.home())}")
        print("  ✅ All data cleared")
    else:
        print("  ℹ️  Memory data preserved (use --all to also clear data)")

    print("Done. You can now run: hermes plugins remove curve-memory")


def cmd_install_wizard(args):
    """交互式安装向导：检查依赖、初始化、配置"""
    import subprocess, json, shutil
    ok, ng, warn = "✅", "❌", "⚠️"
    print("=== Curve Memory 安装向导 ===\n")

    # 1. 检查 Ollama
    print("1️⃣ 检查 Ollama... ", end="", flush=True)
    try:
        r = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            print(f"{ok} ollama 运行中")
        else:
            print(f"{ng} ollama 未运行 (ollama serve)")
    except FileNotFoundError:
        print(f"{ng} Ollama 未安装")
    except Exception as e:
        print(f"{ng} {e}")

    # 2. 检查嵌入模型
    print("2️⃣ 检查 qwen3-embedding:8b... ", end="", flush=True)
    try:
        r = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=10)
        if "qwen3-embedding" in r.stdout:
            print(f"{ok} 已安装")
        else:
            print(f"{warn} 未找到，执行 pull...")
            r2 = subprocess.run(["ollama", "pull", "qwen3-embedding:8b"], timeout=300)
            print(f"   → {'✅ 完成' if r2.returncode == 0 else '❌ 失败'}")
    except Exception as e:
        print(f"{ng} {e}")

    # 3. 检查 numpy
    print("3️⃣ 检查 numpy... ", end="", flush=True)
    try:
        import numpy
        print(f"{ok} {numpy.__version__}")
    except ImportError:
        print(f"{warn} 未安装 (pip install numpy)")

    # 4. 创建目录 + 复制脚本
    print("4️⃣ 初始化目录结构... ", end="", flush=True)
    plugin_core = Path.home() / ".hermes" / "plugins" / "curve-memory" / "curve_memory" / "core"
    scripts_dir = Path.home() / ".hermes" / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    for name in ["curve-memory-forgetting.py", "curve-memory-indexer.py"]:
        src = plugin_core / ("forgetting.py" if "forgetting" in name else "indexer.py")
        dst = scripts_dir / name
        shutil.copy2(str(src), str(dst))
    print(f"{ok}")

    # 5. cron 注册
    print("5️⃣ 检查 cron 任务... ", end="", flush=True)
    cron_file = Path.home() / ".hermes" / "cron" / "jobs.json"
    if cron_file.exists():
        data = json.loads(cron_file.read_text())
        existing = {j.get("name") for j in data.get("jobs", [])}
        new = []
        for jname, script, sched in [
            ("snowlyn-memory-decay", "curve-memory-forgetting.py", "0 3 * * *"),
            ("snowlyn-memory-index", "curve-memory-indexer.py", "45 3 * * *"),
        ]:
            if jname not in existing:
                new.append(jname)
        if new:
            print(f"{warn} 需手动注册: {', '.join(new)}")
            print(f"   运行: curve-memory setup")
        else:
            print(f"{ok} 已注册")
    else:
        print(f"{warn} cron 系统未就绪")

    # 6. 配置检查
    print("6️⃣ 检查 memory.plugin 配置... ", end="", flush=True)
    config_path = Path.home() / ".hermes" / "config.yaml"
    if config_path.exists():
        raw = config_path.read_text()
        if "memory.plugin" in raw or "memory:" in raw:
            print(f"{ok}")
        else:
            print(f"{warn} 未设置 → hermes config set memory.plugin curve-memory")

    # 7. 索引检查
    print("7️⃣ 检查索引状态... ", end="", flush=True)
    emb_dir = Path.home() / ".hermes" / "memories" / ".embedding_index"
    if emb_dir.exists() and any(emb_dir.iterdir()):
        print(f"{ok} 有 {len(list(emb_dir.glob('*.jsonl')))} 个嵌入文件")
    else:
        print(f"{warn} 索引为空 → curve-memory index --rebuild")

    print("\n✅ 检查完成。按上述 ⚠️ 提示操作即可。")


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

    # uninstall
    p_uninstall = sub.add_parser("uninstall", help="卸载：清除软链接、cron、数据")
    p_uninstall.add_argument("--all", action="store_true", help="同时清除记忆数据")
    p_uninstall.add_argument("-y", "--yes", action="store_true", help="跳过确认")
    p_uninstall.set_defaults(func=cmd_uninstall)

    # install-wizard
    p_wizard = sub.add_parser("install-wizard", help="安装向导：检查依赖并初始化")
    p_wizard.set_defaults(func=cmd_install_wizard)

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
