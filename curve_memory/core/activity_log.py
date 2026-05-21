#!/usr/bin/env python3
"""
activity_log.py — 操作日志，用于 undo 和统计
追加写入 ~/.hermes/memories/.activity_log.jsonl
"""

import json
import time
from pathlib import Path
from typing import Optional

LOG_FILE = Path.home() / ".hermes" / "memories" / ".activity_log.jsonl"
MAX_ENTRIES = 1000


def log_operation(op: str, topic: str, detail: str = "", metadata: dict = None):
    """记录一条操作"""
    entry = {
        "ts": time.time(),
        "op": op,
        "topic": topic,
        "detail": detail,
        "metadata": metadata or {},
    }
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    # 修剪旧条目
    _trim()


def _trim():
    if not LOG_FILE.exists():
        return
    lines = LOG_FILE.read_text().strip().splitlines()
    if len(lines) > MAX_ENTRIES:
        LOG_FILE.write_text("\n".join(lines[-MAX_ENTRIES:]) + "\n")


def get_recent_ops(n: int = 10) -> list:
    """获取最近 N 条操作"""
    if not LOG_FILE.exists():
        return []
    lines = LOG_FILE.read_text().strip().splitlines()
    result = []
    for line in lines[-n:]:
        if line.strip():
            result.append(json.loads(line))
    return list(reversed(result))


def get_op_stats() -> dict:
    """获取操作统计"""
    if not LOG_FILE.exists():
        return {}
    stats = {}
    for line in LOG_FILE.read_text().strip().splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        op = entry.get("op", "unknown")
        stats[op] = stats.get(op, 0) + 1
    return stats
