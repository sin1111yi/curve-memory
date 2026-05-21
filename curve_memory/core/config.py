#!/usr/bin/env python3
"""
config.py — curve-memory 配置管理

从 ~/.hermes/config.yaml 读取 memory.curve-memory 配置段。
不依赖 PyYAML（使用 activity.py 的简易解析器）。
"""

from pathlib import Path
from typing import Any, Dict

# 兼容路径
try:
    from activity import parse_activity
except ModuleNotFoundError:
    from curve_memory.core.activity import parse_activity

DEFAULT_CONFIG = {
    "embedding": {
        "provider": "ollama",
        "model": "qwen3-embedding:8b",
        "base_url": "http://localhost:11434",
    },
    "search": {
        "alpha": 0.35,
        "beta": 0.45,
        "gamma": 0.20,
        "top_k": 5,
    },
    "tier": {
        "archive_threshold_days": 30,
        "mature_access_count": 20,
        "mature_t_days": 3,
        "tier_5": 0.800,
        "tier_4": 0.640,
        "tier_3": 0.503,
        "tier_2": 0.465,
    },
}


def load_config() -> dict:
    """加载插件配置，缺失项使用默认值。支持环境变量覆盖。"""
    config_path = Path.home() / ".hermes" / "config.yaml"
    if not config_path.exists():
        cfg = _deep_copy(DEFAULT_CONFIG)
    else:
        try:
            raw = config_path.read_text(encoding="utf-8")
            hermes_cfg = parse_activity(raw)
        except Exception:
            cfg = _deep_copy(DEFAULT_CONFIG)
            cfg = _apply_env_overrides(cfg)
            return cfg

        plugin_cfg = _extract_plugin_config(raw)
        if not plugin_cfg:
            cfg = _deep_copy(DEFAULT_CONFIG)
        else:
            merged = _deep_copy(DEFAULT_CONFIG)
            for section in ("embedding", "search", "tier"):
                if section in plugin_cfg and isinstance(plugin_cfg[section], dict):
                    merged[section].update(plugin_cfg[section])
            cfg = merged

    cfg = _apply_env_overrides(cfg)
    return cfg


def _apply_env_overrides(cfg: dict) -> dict:
    """环境变量覆盖配置"""
    import os
    env_map = {
        "CURVE_MEMORY_EMBEDDING_MODEL": ("embedding", "model"),
        "CURVE_MEMORY_EMBEDDING_URL": ("embedding", "base_url"),
        "CURVE_MEMORY_ALPHA": ("search", "alpha"),
        "CURVE_MEMORY_BETA": ("search", "beta"),
        "CURVE_MEMORY_GAMMA": ("search", "gamma"),
        "CURVE_MEMORY_ARCHIVE_DAYS": ("tier", "archive_threshold_days"),
    }
    for env_name, (section, key) in env_map.items():
        val = os.environ.get(env_name)
        if val is not None:
            try:
                if "." in val:
                    cfg[section][key] = float(val)
                else:
                    cfg[section][key] = int(val)
            except ValueError:
                cfg[section][key] = val
    return cfg


def _extract_plugin_config(raw: str) -> dict:
    """从 YAML 文本中提取 memory.curve-memory 配置块"""
    import re
    # 查找 memory: 块下的 curve-memory: 块
    in_memory = False
    in_curve = False
    result = {}
    current_section = None
    indent_level = 0

    for line in raw.splitlines():
        stripped = line.rstrip()
        if not stripped.strip() or stripped.strip().startswith("#"):
            continue

        # 检测 memory: 顶层键
        m = re.match(r'^memory:\s*', stripped)
        if m:
            in_memory = True
            indent_level = len(line) - len(line.lstrip())
            in_curve = False
            continue

        if not in_memory:
            continue

        # 检测 memory 下的 curve-memory:
        space = len(line) - len(line.lstrip())
        if space > indent_level:
            m = re.match(r'^(\s+)([\w-]+):\s*', stripped)
            if m:
                key = m.group(2)
                if key == "curve-memory":
                    in_curve = True
                    continue

        if in_curve:
            m = re.match(r'^(\s+)(\w+):\s*', stripped)
            if m:
                key = m.group(2)
                if key in ("embedding", "search", "tier"):
                    current_section = key
                    result[current_section] = {}
                elif current_section:
                    val_match = re.match(r'^(\s+)(\w+):\s*(.*)', stripped)
                    if val_match:
                        sub_key = val_match.group(2)
                        sub_val = val_match.group(3).strip()
                        result[current_section][sub_key] = _parse_val(sub_val)

    return result


def _parse_val(val: str):
    if val == "true":
        return True
    if val == "false":
        return False
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val.strip("'\"")


def _deep_copy(d: dict) -> dict:
    return {k: _deep_copy(v) if isinstance(v, dict) else v for k, v in d.items()}


def format_config(cfg: dict) -> str:
    """格式化配置为可读字符串"""
    lines = ["=== Curve Memory Configuration ==="]
    for section, values in cfg.items():
        lines.append(f"\n[{section}]")
        for k, v in values.items():
            lines.append(f"  {k}: {v}")
    return "\n".join(lines)


if __name__ == "__main__":
    cfg = load_config()
    print(format_config(cfg))
