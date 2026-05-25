#!/usr/bin/env python3
#!/usr/bin/env python3
"""activity.py — ACTIVITY.yaml read/write utility

Provides parse_activity / format_activity for other modules.
Also provides parse_timestamp / format_timestamp for unified timestamp handling."""

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def parse_timestamp(val) -> float:
    """Parse timestamp from any format into Unix seconds (float)

    Supported formats:
    - int/float → Unix seconds (return as-is)
    - ISO 8601 string → parse and convert to Unix seconds
    - Pure numeric string → convert to number
    - Otherwise → return 0.0
    """
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        # Try ISO 8601 first
        try:
            dt = datetime.fromisoformat(val)
            return dt.timestamp()
        except (ValueError, TypeError):
            pass
        # Try numeric string (backward compat with old unix timestamps)
        try:
            return float(val)
        except (ValueError, TypeError):
            pass
    return 0.0


def format_timestamp() -> str:
    """Get current timestamp as ISO 8601 format string

    Prefer date -Iseconds (human-readable), fallback to Python datetime.
    """
    import subprocess
    try:
        result = subprocess.run(
            ["date", "-Iseconds"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            ts = result.stdout.strip()
            # date output like "2026-05-26T10:15:00+08:00"
            return ts
    except Exception:
        pass
    # Fallback
    return datetime.now().astimezone().isoformat()


def parse_activity(text: str) -> dict:
    """Manually parse ACTIVITY.yaml"""
    result = {"metadata": {}, "memories": {}}
    current_section = None
    current_memory = None

    for line in text.splitlines():
        stripped = line.rstrip()
        if not stripped or stripped.strip().startswith("#"):
            continue

        # Detect top-level keys
        m = re.match(r'^(\w+):\s*(.*)', stripped)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if key in ("metadata", "memories"):
                current_section = key
                continue
            if current_section == "metadata":
                result["metadata"][key] = _parse_val(val)
                continue

        # Detect topic names under memories
        m = re.match(r'^\s{2}(\S[\w.-]*):', stripped)
        if m and current_section == "memories":
            current_memory = m.group(1)
            result["memories"][current_memory] = {}
            continue

        # Detect memory fields
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
    """Format dict as YAML string"""
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


def load_activity(memories_dir: Optional[Path] = None) -> dict:
    """Load ACTIVITY.yaml"""
    if memories_dir is None:
        path = Path.home() / ".hermes" / "memories" / "ACTIVITY.yaml"
    else:
        path = Path(memories_dir) / "ACTIVITY.yaml"
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8")
    return parse_activity(raw)
