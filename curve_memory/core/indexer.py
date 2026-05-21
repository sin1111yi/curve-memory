#!/usr/bin/env python3
"""
curve-memory-indexer.py — Embedding + FTS5 索引构建/增量更新

用法：
  python3 curve-memory-indexer.py --rebuild    # 全量重建
  python3 curve-memory-indexer.py --incremental # 增量更新（按 mtime）
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

# 当从 ~/.hermes/scripts/ 独立运行时，添加插件路径
_SCRIPT_DIR = Path(__file__).resolve().parent
_PLUGIN_CORE_DIR = Path.home() / ".hermes" / "plugins" / "curve-memory"
if _PLUGIN_CORE_DIR.exists() and str(_PLUGIN_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_CORE_DIR))
_PARENT = _SCRIPT_DIR.parent.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

LOCK_TIMEOUT = 1800  # 30 分钟超时

# 将脚本目录加入 path
pass # path managed by plugin system
from curve_memory.core.chunker import chunk_file, chunk_tier_summary
from curve_memory.core.tier import r_to_tier_level, forgetting_curve
from curve_memory.core.activity import load_activity, parse_activity

MEMORIES_DIR = Path(os.environ.get("HOME", "~")) / ".hermes" / "memories"
ACTIVE_DIR = MEMORIES_DIR / "active"
EMBEDDING_DIR = MEMORIES_DIR / ".embedding_index"
FTS5_DIR = MEMORIES_DIR / ".fts5"
FTS5_PATH = FTS5_DIR / "curve_memory_fts5.db"
EMBEDDING_META = MEMORIES_DIR / ".embedding_meta.yaml"
LOCK_FILE = MEMORIES_DIR / ".memory.lock"
ACTIVITY_FILE = MEMORIES_DIR / "ACTIVITY.yaml"

# 缓存文件 mtime，用于增量检测
MTIME_CACHE = MEMORIES_DIR / ".mtime_cache.json"

logs = []


def acquire_lock() -> bool:
    if LOCK_FILE.exists():
        return False
    LOCK_FILE.write_text(str(os.getpid()))
    return True


def release_lock():
    LOCK_FILE.unlink(missing_ok=True)


def load_mtime_cache() -> dict:
    if MTIME_CACHE.exists():
        return json.loads(MTIME_CACHE.read_text())
    return {}


def save_mtime_cache(cache: dict):
    MTIME_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2))


def get_active_topics() -> list:
    """从 ACTIVE_DIR 获取所有 .md 文件"""
    return sorted([f.stem for f in ACTIVE_DIR.glob("*.md") if f.is_file()])


def get_embedder() -> list:
    """获取 embedding provider"""
    try:
        from curve_memory.core.embedding_provider import create_embedding_provider
        return create_embedding_provider()
    except Exception:
        return None


def embed_and_index(topic: str, content: str, tier_level: int, embedder, conn: sqlite3.Connection):
    """
    对单个 topic 构建 embedding 和 FTS5 索引。
    根据 TIER 级别决定索引粒度。
    """
    # 分块
    chunks = chunk_tier_summary(topic, content, tier_level)

    # Embedding 索引
    if embedder:
        embedding_file = EMBEDDING_DIR / f"{topic}.jsonl"
        with open(embedding_file, "w", encoding="utf-8") as f:
            for chunk in chunks:
                text = chunk["text"]
                if not text.strip():
                    continue
                try:
                    vector = embedder.embed(text)
                except Exception as e:
                    logs.append(f"  ⚠️  {topic}/{chunk['chunk']}: embed failed: {e}")
                    continue
                record = {
                    "topic": topic,
                    "chunk": chunk["chunk"],
                    "text": text,
                    "vector": vector,
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        logs.append(f"  📦 {topic}: {len(chunks)} chunks embedded")

    # FTS5 索引
    _build_fts5_for_topic(conn, topic, content)


def _build_fts5_for_topic(conn: sqlite3.Connection, topic: str, content: str):
    """构建单个 topic 的 FTS5 索引"""
    # 删除旧的
    conn.execute("DELETE FROM memory_fts WHERE topic = ?", (topic,))
    # 插入新的
    conn.execute(
        "INSERT INTO memory_fts (topic, content) VALUES (?, ?)",
        (topic, content)
    )
    conn.commit()


def init_fts5(conn: sqlite3.Connection):
    """初始化 FTS5 表"""
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
            topic,
            content,
            tokenize='unicode61'
        )
    """)
    conn.commit()


def rebuild_index(embedder=None):
    """全量重建所有索引"""
    FTS5_DIR.mkdir(parents=True, exist_ok=True)
    EMBEDDING_DIR.mkdir(parents=True, exist_ok=True)
    activity = load_activity()

    conn = sqlite3.connect(str(FTS5_PATH))
    init_fts5(conn)

    topics = get_active_topics()
    logs.append(f"📊  Rebuilding index for {len(topics)} topics")

    for topic in topics:
        filepath = ACTIVE_DIR / f"{topic}.md"
        if not filepath.exists():
            continue

        content = filepath.read_text(encoding="utf-8")

        # 获取 TIER 级别
        info = activity.get(topic, {})
        t_val = info.get("t", 0)
        r = forgetting_curve(t_val)
        tier_level = r_to_tier_level(r)

        embed_and_index(topic, content, tier_level, embedder, conn)
        logs.append(f"  ✅ {topic}: indexed (tier={tier_level})")

    # 清理不存在的 topic 索引
    _cleanup_stale_indexes(set(topics))

    conn.close()

    # 更新元数据
    meta = {
        "format": "forgetting-curve-v1",
        "embedding_provider": embedder.name if embedder else "none",
        "embedding_dim": embedder.dim if embedder else 0,
        "topic_count": len(topics),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    EMBEDDING_META.write_text(
        "\n".join(f"{k}: {v}" for k, v in meta.items()) + "\n",
        encoding="utf-8"
    )


def incremental_index(embedder=None):
    """增量更新（只处理 mtime 变化的文件）"""
    FTS5_DIR.mkdir(parents=True, exist_ok=True)
    EMBEDDING_DIR.mkdir(parents=True, exist_ok=True)
    activity = load_activity()

    mtime_cache = load_mtime_cache()
    conn = sqlite3.connect(str(FTS5_PATH))
    init_fts5(conn)

    topics = get_active_topics()
    changed = 0
    skipped = 0

    for topic in topics:
        filepath = ACTIVE_DIR / f"{topic}.md"
        if not filepath.exists():
            continue

        current_mtime = str(filepath.stat().st_mtime)
        cached_mtime = mtime_cache.get(topic)

        if cached_mtime == current_mtime:
            skipped += 1
            continue

        content = filepath.read_text(encoding="utf-8")
        info = activity.get(topic, {})
        t_val = info.get("t", 0)
        r = forgetting_curve(t_val)
        tier_level = r_to_tier_level(r)

        embed_and_index(topic, content, tier_level, embedder, conn)
        mtime_cache[topic] = current_mtime
        changed += 1
        logs.append(f"  🔄 {topic}: updated (tier={tier_level})")

    # 清理
    _cleanup_stale_indexes(set(topics), mtime_cache)

    save_mtime_cache(mtime_cache)
    conn.close()

    logs.append(f"📊  Incremental: {changed} changed, {skipped} skipped")


def _cleanup_stale_indexes(active_topics: set, mtime_cache: dict = None):
    """清理已归档/删除 topic 的索引文件"""
    # 清理 embedding
    for fpath in EMBEDDING_DIR.glob("*.jsonl"):
        topic = fpath.stem
        if topic not in active_topics:
            fpath.unlink()
            logs.append(f"  🗑️  {topic}: cleaned embedding index")

    # 清理 FTS5 中已删除的 topic
    if FTS5_PATH.exists():
        conn = sqlite3.connect(str(FTS5_PATH))
        cursor = conn.execute("SELECT DISTINCT topic FROM memory_fts")
        for row in cursor.fetchall():
            if row[0] not in active_topics:
                conn.execute("DELETE FROM memory_fts WHERE topic = ?", (row[0],))
        conn.commit()
        conn.close()

    # 清理 mtime cache
    if mtime_cache:
        stale = [t for t in mtime_cache if t not in active_topics]
        for t in stale:
            del mtime_cache[t]


def check_status() -> dict:
    """检查索引状态"""
    active_count = len(get_active_topics())
    embedding_count = len(list(EMBEDDING_DIR.glob("*.jsonl"))) if EMBEDDING_DIR.exists() else 0
    fts5_exists = FTS5_PATH.exists()
    embedder = get_embedder()

    return {
        "active_topics": active_count,
        "embedding_files": embedding_count,
        "fts5_ready": fts5_exists,
        "embedder_ready": embedder is not None,
        "embedder_name": embedder.name if embedder else "none",
        "embedding_dim": embedder.dim if embedder else 0,
    }


def main():
    parser = argparse.ArgumentParser(description="Curve Memory Indexer")
    parser.add_argument("--rebuild", action="store_true", help="全量重建索引")
    parser.add_argument("--incremental", action="store_true", help="增量更新")
    parser.add_argument("--status", action="store_true", help="查看索引状态")
    args = parser.parse_args()

    if args.status:
        status = check_status()
        print("=== Index Status ===")
        for k, v in status.items():
            print(f"  {k}: {v}")
        return

    if not acquire_lock():
        print("❌ Lock file exists")
        sys.exit(1)

    try:
        embedder = get_embedder()

        if args.rebuild:
            rebuild_index(embedder)
        elif args.incremental:
            incremental_index(embedder)
        else:
            # 默认增量
            incremental_index(embedder)

    finally:
        release_lock()

    for log in logs:
        print(log)


if __name__ == "__main__":
    main()
