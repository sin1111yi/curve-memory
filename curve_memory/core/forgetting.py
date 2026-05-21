#!/usr/bin/env python3
"""
curve-memory-forgetting.py — 每日遗忘曲线衰减守护

每天凌晨 3 点执行：
1. 读取 ACTIVITY.yaml（v3 格式）
2. 所有记忆 t += 1（受保护记忆除外）
3. 计算 R(t)，更新 TIER
4. 成熟度检测
5. 执行归档（遗忘归档 or 成熟归档）
6. 写回 ACTIVITY.yaml
7. 记录事件日志

输出：操作日志，无变更时静默。
"""

import os
import shutil
import sys
import math
import time
from datetime import datetime
from pathlib import Path

# 当从 ~/.hermes/scripts/ 独立运行时，添加插件路径
_SCRIPT_DIR = Path(__file__).resolve().parent
_PLUGIN_CORE_DIR = Path.home() / ".hermes" / "plugins" / "curve-memory"
if _PLUGIN_CORE_DIR.exists() and str(_PLUGIN_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_CORE_DIR))
_PARENT = _SCRIPT_DIR.parent.parent  # plugins/curve-memory/
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

LOCK_TIMEOUT = 1800  # 30 分钟超时

from curve_memory.core.tier import forgetting_curve, r_to_tier_name, should_archive, is_mature, BASE_RATE, EPSILON
from curve_memory.core.activity import parse_activity, format_activity

MEMORIES_DIR = Path(os.environ.get("HOME", "~")) / ".hermes" / "memories"
MEMORY_FILE = MEMORIES_DIR / "MEMORY.md"
ACTIVITY_FILE = MEMORIES_DIR / "ACTIVITY.yaml"
ACTIVE_DIR = MEMORIES_DIR / "active"
ARCHIVE_FORGOTTEN_DIR = MEMORIES_DIR / "archive" / "forgotten"
ARCHIVE_MATURE_DIR = MEMORIES_DIR / "archive" / "mature"
KNOWLEDGE_DIR = Path(os.environ.get("HOME", "~")) / ".hermes" / "knowledge"
FORGET_LOG = MEMORIES_DIR / "archive" / "FORGET_LOG.md"
LOCK_FILE = MEMORIES_DIR / ".memory.lock"

logs = []


def acquire_lock() -> bool:
    """获取文件锁，30 分钟超时"""
    if LOCK_FILE.exists():
        age = time.time() - LOCK_FILE.stat().st_mtime
        if age > LOCK_TIMEOUT:
            logs.append(f"⚠️  Stale lock (age={age:.0f}s), removing")
            LOCK_FILE.unlink()
        else:
            return False
    LOCK_FILE.write_text(str(os.getpid()))
    return True


def release_lock():
    LOCK_FILE.unlink(missing_ok=True)


def read_memory_index() -> list:
    if not MEMORY_FILE.exists():
        return []
    raw = MEMORY_FILE.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    return [e.strip() for e in raw.split("\n§\n") if e.strip()]


def write_memory_index(entries: list):
    content = "\n§\n".join(entries)
    MEMORY_FILE.write_text(content + "\n", encoding="utf-8")


def write_forget_log(topic: str, t: int, r: float, reason: str):
    ARCHIVE_FORGOTTEN_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"| {now} | {topic} | t={t} | R={r:.4f} | {reason} |\n"
    if not FORGET_LOG.exists():
        header = """# Memory Forgetting Log (Forgetting Curve v3)
| 时间 | 主题 | t值 | R(t) | 原因 |
|------|------|-----|------|------|
"""
        FORGET_LOG.write_text(header + entry, encoding="utf-8")
    else:
        with open(FORGET_LOG, "a", encoding="utf-8") as f:
            f.write(entry)


def forget_archive(topic: str, t: int, r: float, data: dict):
    """遗忘归档：移到 archive/forgotten/"""
    src = ACTIVE_DIR / f"{topic}.md"
    dst = ARCHIVE_FORGOTTEN_DIR / f"{topic}.md"
    if src.exists():
        ARCHIVE_FORGOTTEN_DIR.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        logs.append(f"📦  {topic}: moved to archive/forgotten/")
    else:
        logs.append(f"⚠️  {topic}: active/{topic}.md not found")

    # 从 MEMORY.md 移除索引
    entries = read_memory_index()
    new_entries = [e for e in entries if not e.startswith(f"idx:{topic}")]
    if len(new_entries) != len(entries):
        write_memory_index(new_entries)
        logs.append(f"🗑️  {topic}: removed from MEMORY.md")

    # 从 ACTIVITY.yaml 删除
    if topic in data.get("memories", {}):
        del data["memories"][topic]
        logs.append(f"🗑️  {topic}: removed from ACTIVITY.yaml")

    write_forget_log(topic, t, r, "forgotten")


def mature_archive(topic: str, t: int, r: float, data: dict, info: dict):
    """成熟归档：复制到 archive/mature/ 并创建 knowledge/ 文档"""
    src = ACTIVE_DIR / f"{topic}.md"
    # 复制到成熟归档
    ARCHIVE_MATURE_DIR.mkdir(parents=True, exist_ok=True)
    if src.exists():
        shutil.copy(str(src), str(ARCHIVE_MATURE_DIR / f"{topic}.md"))
        logs.append(f"🎓  {topic}: copied to archive/mature/")

    # 创建 knowledge 文档
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    knowledge_path = KNOWLEDGE_DIR / f"{topic}.md"
    original_content = src.read_text(encoding="utf-8") if src.exists() else ""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    knowledge_content = f"""# {topic}

**来源：** curve-memory mature promotion
**固化时间：** {now}
**访问次数：** {info.get('access_count', 0)}
**原始存档：** archive/mature/{topic}.md
**注意：** 此文件由遗忘曲线系统自动生成，agent 应在下次使用时提取核心知识并替换此文件内容。

---

{original_content}
"""
    knowledge_path.write_text(knowledge_content, encoding="utf-8")
    logs.append(f"📚  {topic}: knowledge doc created at {knowledge_path}")

    # 删除 active/
    if src.exists():
        src.unlink()

    # 从 MEMORY.md 移除索引
    entries = read_memory_index()
    new_entries = [e for e in entries if not e.startswith(f"idx:{topic}")]
    if len(new_entries) != len(entries):
        write_memory_index(new_entries)

    # 从 ACTIVITY.yaml 删除
    if topic in data.get("memories", {}):
        del data["memories"][topic]

    write_forget_log(topic, t, r, "mature archived")


def main():
    run_time = datetime.now()
    logs.append(f"=== Forgetting Curve Run: {run_time} ===")

    if not acquire_lock():
        logs.append("❌ Lock file exists, another instance is running")
        _deliver(logs)
        return

    try:
        if not ACTIVITY_FILE.exists():
            logs.append("❌ ACTIVITY.yaml not found, aborting")
            _deliver(logs)
            return

        raw = ACTIVITY_FILE.read_text(encoding="utf-8")
        data = parse_activity(raw)
        memories = data.get("memories", {})

        if not memories:
            logs.append("ℹ️  No memory entries in ACTIVITY.yaml")
            _deliver(logs)
            return

        logs.append(f"📊  Loaded {len(memories)} memories")

        to_forget = []
        to_mature = []

        for topic, info in sorted(memories.items()):
            # 受保护记忆跳过
            if info.get("protected", False):
                logs.append(f"🛡️  {topic}: protected, skipping")
                continue

            # t += 1
            ct = info.get("t", 0) + 1
            info["t"] = ct
            r = forgetting_curve(ct)

            # 成熟度检测
            if not info.get("mature", False):
                ac = info.get("access_count", 0)
                if is_mature(ac, ct):
                    info["mature"] = True
                    logs.append(f"🌟  {topic}: matured (access_count={ac}, t={ct})")

            # 归档判定
            if should_archive(ct):
                if info.get("mature", False):
                    to_mature.append((topic, ct, r, info))
                else:
                    to_forget.append((topic, ct, r))

            logs.append(f"📈  {topic}: t={ct}, R={r:.6f} ({r_to_tier_name(r)})" +
                        (" 🎓mature" if info.get("mature") else "") +
                        (" 🔜archive" if should_archive(ct) else ""))

        # 执行归档
        for topic, ct, r in to_forget:
            forget_archive(topic, ct, r, data)
        for topic, ct, r, info in to_mature:
            mature_archive(topic, ct, r, data, info)

        # 写回
        ACTIVITY_FILE.write_text(format_activity(data), encoding="utf-8")
        logs.append(f"✅  ACTIVITY.yaml updated ({len(data.get('memories', {}))} active memories)")

        if to_forget or to_mature:
            logs.append(f"📊  Total archived: {len(to_forget)} forgotten + {len(to_mature)} mature")
        else:
            logs.append("🟢  No memories exceeded threshold")

    finally:
        release_lock()

    _deliver(logs)


def _deliver(log_lines: list):
    output = "\n".join(log_lines)
    if "📦" in output or "🎓" in output or "⚠️" in output or "❌" in output or "📊" in output:
        print(output)


if __name__ == "__main__":
    main()
