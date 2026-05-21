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
        "indexer", os.path.join(os.path.dirname(__file__), "core", "indexer.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.argv = ["curve-memory-indexer"]
    if args.rebuild:
        sys.argv.append("--rebuild")
    else:
        sys.argv.append("--incremental")
    mod.main()


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
        "forgetting", os.path.join(os.path.dirname(__file__), "core", "forgetting.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.main()


def cmd_forget(args):
    from activity import parse_activity, format_activity
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "forgetting", os.path.join(os.path.dirname(__file__), "core", "forgetting.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    forget_archive = mod.forget_archive
    from curve_memory.core.tier import forgetting_curve
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


def cmd_repair(args):
    """修复：检查 ACTIVITY.yaml、清理锁、重建索引"""
    import json, shutil
    memories_dir = Path.home() / ".hermes" / "memories"
    activity_path = memories_dir / "ACTIVITY.yaml"
    lock_path = memories_dir / ".memory.lock"
    found_issues = 0

    print("=== Repair ===")

    # 1. 检查 ACTIVITY.yaml 版本
    print("1️⃣  ACTIVITY.yaml... ", end="", flush=True)
    if activity_path.exists():
        raw = activity_path.read_text(encoding="utf-8")
        if "format_version:" in raw:
            # 提取版本
            import re
            m = re.search(r'format_version:\s*(\d+)', raw)
            ver = int(m.group(1)) if m else 0
            if ver < 3:
                print(f"⚠️  v{ver} → 需要迁移")
                found_issues += 1
            else:
                print(f"✅ v{ver}")
        else:
            print("⚠️  无法识别格式")
            found_issues += 1
    else:
        print("❌ 不存在")
        found_issues += 1

    # 2. 检查锁文件
    print("2️⃣  Lock file... ", end="", flush=True)
    if lock_path.exists():
        pid = lock_path.read_text().strip()
        import subprocess
        r = subprocess.run(["ps", "-p", pid], capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            lock_path.unlink()
            print(f"✅ 已清理僵尸锁 (PID {pid})")
        else:
            print(f"🔒 进程 {pid} 仍在运行")
    else:
        print("✅ 无残留")

    # 3. 检查嵌入索引完整性
    print("3️⃣  Embedding index... ", end="", flush=True)
    emb_dir = memories_dir / ".embedding_index"
    if emb_dir.exists():
        broken = 0
        for f in emb_dir.glob("*.jsonl"):
            try:
                for line in f.read_text().strip().splitlines():
                    if line.strip():
                        json.loads(line)
            except (json.JSONDecodeError, Exception):
                broken += 1
                f.unlink()
        if broken:
            print(f"⚠️  修复 {broken} 个损坏文件")
            found_issues += 1
        else:
            print(f"✅ {len(list(emb_dir.glob('*.jsonl')))} 个文件正常")
    else:
        print("⚠️  索引目录不存在")

    # 4. 检查 FTS5
    print("4️⃣  FTS5 index... ", end="", flush=True)
    fts5_path = memories_dir / ".fts5" / "curve_memory_fts5.db"
    if fts5_path.exists():
        import sqlite3
        try:
            conn = sqlite3.connect(str(fts5_path))
            conn.execute("SELECT count(*) FROM memory_fts")
            conn.close()
            print("✅")
        except Exception:
            print("⚠️  损坏，重建")
            fts5_path.unlink()
            found_issues += 1
    else:
        print("⚠️  不存在")

    # 5. 自动修复
    if args.fix:
        print("\n🛠️  自动修复:")
        if not activity_path.exists():
            print("   ACTIVITY.yaml 丢失 → 跳过")
        if lock_path.exists():
            lock_path.unlink()
        if found_issues > 0:
            print(f"   建议运行: curve-memory index --rebuild")

    if found_issues == 0:
        print("\n✅ 一切正常")
    else:
        print(f"\n⚠️  发现 {found_issues} 个问题" + ("，已部分修复" if args.fix else "，用 --fix 自动修复"))
    print(f"   Lock: {lock_path}")


def cmd_recover(args):
    """恢复：从 archive/ 恢复已归档的记忆"""
    import shutil
    memories_dir = Path.home() / ".hermes" / "memories"
    activity_path = memories_dir / "ACTIVITY.yaml"

    # 搜索所有归档中的主题
    candidates = []
    for archive_dir in [memories_dir / "archive" / "forgotten", memories_dir / "archive" / "mature"]:
        if archive_dir.exists():
            for f in archive_dir.glob("*.md"):
                if f.name != "FORGET_LOG.md":
                    candidates.append((f.stem, archive_dir.name, f))

    if args.list:
        print("=== Archived Topics ===")
        for topic, kind, path in sorted(candidates):
            print(f"  {topic:25s} ({kind})")
        return

    if not args.topic:
        print(f"可用主题: {', '.join(sorted(set(t for t,_,_ in candidates)))}")
        print("使用: curve-memory recover <topic>")
        return

    # 查找主题
    found = [c for c in candidates if c[0] == args.topic]
    if not found:
        print(f"❌ 未找到 '{args.topic}' 在 archive/ 中")
        return

    for topic, kind, src_path in found:
        dst_path = memories_dir / "active" / f"{topic}.md"
        if dst_path.exists():
            print(f"⚠️  active/{topic}.md 已存在，跳过")
            continue
        shutil.copy2(str(src_path), str(dst_path))
        print(f"✅ 已从 archive/{kind}/ 恢复: {topic}")

    print("ℹ️  需手动添加索引: curve-memory index --rebuild")


def cmd_config(args):
    """查看当前配置"""
    try:
        from curve_memory.core.config import load_config, format_config
        cfg = load_config()
        print(format_config(cfg))
        print("\n要修改配置，编辑 ~/.hermes/config.yaml 中的 memory.curve-memory 段。")
    except Exception as e:
        print(f"加载配置失败: {e}")


def cmd_deactivate(args):
    """停用：重设 memory.plugin，保留数据"""
    import subprocess
    try:
        subprocess.run(["hermes", "config", "unset", "memory.plugin"],
                       capture_output=True, timeout=10)
        print("✅ curve-memory 已停用（数据已保留）")
        print("   启用: curve-memory activate")
    except Exception as e:
        print(f"⚠️  {e}")
        print("   手动: hermes config unset memory.plugin")


def cmd_activate(args):
    """重新激活"""
    import subprocess
    try:
        subprocess.run(["hermes", "config", "set", "memory.plugin", "curve-memory"],
                       capture_output=True, timeout=10)
        print("✅ curve-memory 已启用")
        print("   重启: hermes gateway restart")
    except Exception as e:
        print(f"⚠️  {e}")
        print("   手动: hermes config set memory.plugin curve-memory")


def cmd_undo(args):
    """撤销最近的 touch/forget 操作"""
    try:
        from curve_memory.core.activity_log import get_recent_ops
        from curve_memory.core.activity import parse_activity, format_activity
    except ModuleNotFoundError:
        from activity_log import get_recent_ops
        from activity import parse_activity, format_activity

    ops = get_recent_ops(10)
    if not ops:
        print("没有可撤销的操作")
        return

    print("最近操作:")
    for i, op in enumerate(ops):
        ts = op.get("ts", 0)
        from datetime import datetime
        tstr = datetime.fromtimestamp(ts).strftime("%H:%M")
        print(f"  [{i}] {tstr} {op['op']:8s} {op['topic']}")

    print("\n撤销: curve-memory touch <topic> 或 curve-memory recover <topic>")


def cmd_stats(args):
    """详细统计"""
    from curve_memory.core.activity_log import get_op_stats, get_recent_ops
    from curve_memory.core.tier import forgetting_curve, r_to_tier_name
    from curve_memory.core.activity import parse_activity

    activity_path = Path.home() / ".hermes" / "memories" / "ACTIVITY.yaml"
    if activity_path.exists():
        raw = activity_path.read_text()
        data = parse_activity(raw)
        memories = data.get("memories", {})
        active_count = len(memories)

        # TIER 分布
        tier_dist = {}
        total_t = 0
        for topic, info in memories.items():
            t = info.get("t", 0)
            total_t += t
            r = forgetting_curve(t)
            tier = r_to_tier_name(r)
            tier_dist[tier] = tier_dist.get(tier, 0) + 1

        avg_t = total_t / active_count if active_count else 0

        print("=== Curve Memory Stats ===")
        print(f"  活跃记忆:     {active_count}")
        print(f"  平均 t 值:    {avg_t:.1f} 天")
        print(f"  平均 R(t):    {forgetting_curve(avg_t):.4f}")
        print(f"\n  TIER 分布:")
        for tier in ["TIER_5 🔥", "TIER_4 📗", "TIER_3 📙", "TIER_2 📕", "TIER_1 📦", "ARCHIVE 🗄️"]:
            c = tier_dist.get(tier, 0)
            bar = "█" * c
            print(f"    {tier:15s}: {c:2d} {bar}")

    else:
        print("ACTIVITY.yaml 不存在")

    # 操作统计
    op_stats = get_op_stats()
    if op_stats:
        print(f"\n  操作统计:")
        for op, count in sorted(op_stats.items(), key=lambda x: -x[1]):
            print(f"    {op}: {count}")

    # 索引
    emb_dir = Path.home() / ".hermes" / "memories" / ".embedding_index"
    if emb_dir.exists():
        size = sum(f.stat().st_size for f in emb_dir.glob("*.jsonl"))
        print(f"\n  嵌入索引:     {len(list(emb_dir.glob('*.jsonl')))} 文件, {size/1024:.0f} KB")


def cmd_export(args):
    """导出记忆数据为 tar.gz"""
    import tarfile, tempfile
    memories_dir = Path.home() / ".hermes" / "memories"
    knowledge_dir = Path.home() / ".hermes" / "knowledge"
    output = args.output

    print(f"导出到 {output}...")
    with tarfile.open(output, "w:gz") as tar:
        for d in [memories_dir / "active", memories_dir / "archive",
                   memories_dir / "ACTIVITY.yaml", memories_dir / "MEMORY.md",
                   knowledge_dir]:
            if d.exists():
                tar.add(str(d), arcname=str(d.relative_to(Path.home() / ".hermes")))
    print(f"✅ 导出完成 ({Path(output).stat().st_size / 1024:.0f} KB)")


def cmd_plot(args):
    """ASCII 显示 R(t) 曲线"""
    from curve_memory.core.tier import forgetting_curve, r_to_tier_name

    print("=== R(t) 遗忘曲线 ===")
    print("R(t) = 0.462 + 0.538 * exp(-t / 2.71)")
    print()
    print("天  R(t)    TIER")
    print("-" * 30)
    for t in range(0, 35, 1):
        r = forgetting_curve(t)
        tier = r_to_tier_name(r)
        bar_len = int((r - 0.462) / 0.538 * 30)
        bar = "█" * bar_len
        print(f"{t:2d}  {r:.4f}  {tier:12s} {bar}")
    print()
    print("图例: 每个 █ 代表约 3.3% 的保留率")


def cmd_install_wizard(args):
    """交互式安装向导：检查依赖、初始化、配置"""
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

    # repair
    p_repair = sub.add_parser("repair", help="修复：检查并修复常见问题")
    p_repair.add_argument("--fix", action="store_true", help="自动修复")
    p_repair.set_defaults(func=cmd_repair)

    # recover
    p_recover = sub.add_parser("recover", help="从 archive/ 恢复已归档的记忆")
    p_recover.add_argument("topic", nargs="?", help="主题名称")
    p_recover.add_argument("--list", action="store_true", help="列出可恢复的主题")
    p_recover.set_defaults(func=cmd_recover)

    # config
    p_config = sub.add_parser("config", help="查看当前配置")
    p_config.set_defaults(func=cmd_config)

    # deactivate
    p_deact = sub.add_parser("deactivate", help="停用曲线记忆系统（保留数据）")
    p_deact.set_defaults(func=cmd_deactivate)

    # activate
    p_act = sub.add_parser("activate", help="重新激活曲线记忆系统")
    p_act.set_defaults(func=cmd_activate)

    # undo
    p_undo = sub.add_parser("undo", help="撤销最近的操作")
    p_undo.set_defaults(func=cmd_undo)

    # stats
    p_stats = sub.add_parser("stats", help="详细统计信息")
    p_stats.set_defaults(func=cmd_stats)

    # export
    p_export = sub.add_parser("export", help="导出记忆数据")
    p_export.add_argument("output", nargs="?", default="curve-memory-export.tar.gz", help="输出文件路径")
    p_export.set_defaults(func=cmd_export)

    # plot
    p_plot = sub.add_parser("plot", help="显示 R(t) 曲线 ASCII 图")
    p_plot.set_defaults(func=cmd_plot)

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
