#!/usr/bin/env python3
"""
activity.py — ACTIVITY.yaml 读写工具

提供 parse_activity / format_activity 供其他模块共用。
"""

import re
from pathlib import Path
from typing import Optional


def parse_activity(text: str) -> dict:
    """手动解析 ACTIVITY.yaml"""
    result = {"metadata": {}, "memories": {}}
    current_section = None
    current_memory = None

    for line in text.splitlines():
        stripped = line.rstrip()
        if not stripped or stripped.strip().startswith("#"):
            continue

        # 检测顶级键
        m = re.match(r'^(\w+):\s*(.*)', stripped)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if key in ("metadata", "memories"):
                current_section = key
                continue
            if current_section == "metadata":
                result["metadata"][key] = _parse_val(val)
                continue

        # 检测 memories 下的 topic 名
        m = re.match(r'^\s{2}(\S[\w.-]*):', stripped)
        if m and current_section == "memories":
            current_memory = m.group(1)
            result["memories"][current_memory] = {}
            continue

        # 检测记忆字段
        if current_memory:
            m = re.match(r'^\s{4}(\w+):\s*(.*)', stripped)
            if m:
                key, val = m.group(1), m.group(2).strip()
                result["memories"][current_memory][key] = _parse_val(val)

    return result


def _parse_val(val: str):
    if val == "true":
        return True
    if val == "false":
        return False
    if val == "null" or val == "~":
        return None
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val.strip("'\"")


def format_activity(data: dict) -> str:
    """将 dict 格式化为 YAML 字符串"""
    lines = []
    lines.append("metadata:")
    for k, v in data.get("metadata", {}).items():
        lines.append(f"  {k}: {_fmt_val(v)}")
    lines.append("memories:")
    for topic in sorted(data.get("memories", {}).keys()):
        lines.append(f"  {topic}:")
        for k, v in data["memories"][topic].items():
            lines.append(f"    {k}: {_fmt_val(v)}")
    return "\n".join(lines) + "\n"


def _fmt_val(v):
    if v is True:
        return "true"
    if v is False:
        return "false"
    if v is None:
        return "null"
    return str(v)


def load_activity(path: Optional[Path] = None) -> dict:
    """加载 ACTIVITY.yaml"""
    if path is None:
        path = Path.home() / ".hermes" / "memories" / "ACTIVITY.yaml"
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8")
    return parse_activity(raw)
