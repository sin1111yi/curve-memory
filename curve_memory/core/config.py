#!/usr/bin/env python3
"""
config.py — curve-memory 配置管理

支持 hermes_home 参数（不硬编码 ~/.hermes），
环境变量覆盖，提供 get_config_schema / save_config 兼容接口。
"""

import os
import json
from pathlib import Path
from typing import Any, Dict, Optional


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

# 配置段在 config.yaml 中的键名（用于解析和写入）
CONFIG_SECTION_KEY = "curve-memory"
CONFIG_FILE_NAME = "curve-memory-config.json"


def get_config_path(hermes_home: str = "") -> Path:
    """返回配置文件的路径"""
    base = Path(hermes_home).expanduser().resolve() if hermes_home else Path.home() / ".hermes"
    return base / CONFIG_FILE_NAME


def load_config(hermes_home: str = "") -> dict:
    """加载配置，缺失项使用默认值。支持环境变量覆盖。"""
    cfg = _deep_copy(DEFAULT_CONFIG)

    # 尝试从 JSON 配置文件加载
    config_path = get_config_path(hermes_home)
    if config_path.exists():
        try:
            user_cfg = json.loads(config_path.read_text(encoding="utf-8"))
            for section in ("embedding", "search", "tier"):
                if section in user_cfg and isinstance(user_cfg[section], dict):
                    cfg[section].update(user_cfg[section])
        except Exception:
            pass

    cfg = _apply_env_overrides(cfg)
    return cfg


def save_config(values: dict, hermes_home: str = "") -> None:
    """保存配置到 JSON 文件"""
    config_path = get_config_path(hermes_home)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")


def get_config_schema() -> list:
    """返回配置 schema（供 MemoryProvider ABC 使用 / 直接 CLI 调用）"""
    return [
        {
            "key": "model",
            "description": "Ollama embedding model (e.g. qwen3-embedding:8b, nomic-embed-text)",
            "default": "qwen3-embedding:8b",
        },
        {
            "key": "base_url",
            "description": "Ollama server URL",
            "default": "http://localhost:11434",
        },
        {
            "key": "search_alpha",
            "description": "BM25 weight in hybrid search (0-1)",
            "default": 0.35,
        },
        {
            "key": "search_beta",
            "description": "Embedding weight in hybrid search (0-1)",
            "default": 0.45,
        },
        {
            "key": "search_gamma",
            "description": "Recency weight in hybrid search (0-1)",
            "default": 0.20,
        },
        {
            "key": "archive_days",
            "description": "Days before a memory is archived (0 = never)",
            "default": 30,
        },
    ]


def schema_values_to_config(values: dict) -> dict:
    """将 get_config_schema() 的 values 转为内部配置格式"""
    return {
        "embedding": {
            "provider": "ollama",
            "model": values.get("model", "qwen3-embedding:8b"),
            "base_url": values.get("base_url", "http://localhost:11434"),
        },
        "search": {
            "alpha": float(values.get("search_alpha", 0.35)),
            "beta": float(values.get("search_beta", 0.45)),
            "gamma": float(values.get("search_gamma", 0.20)),
            "top_k": 5,
        },
        "tier": {
            "archive_threshold_days": int(values.get("archive_days", 30)),
            "mature_access_count": 20,
            "mature_t_days": 3,
        },
    }


def _apply_env_overrides(cfg: dict) -> dict:
    """环境变量覆盖配置"""
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
