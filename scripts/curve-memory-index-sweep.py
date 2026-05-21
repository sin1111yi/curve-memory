#!/usr/bin/env python3
"""Curve-memory index sweep — standalone script for cron.

Scans active/ directory for memory files missing or stale embedding indexes,
computes new embeddings, and cleans up stale index files for removed topics.

Designed to run as a no_agent cron script. Relies on curve-memory-config.json
for embedding provider settings. Expects to be run from ~/.hermes/scripts/ or
via the Hermes cron scheduler (which auto-resolves relative paths under
~/.hermes/scripts/).

Exit codes:
  0 — success (may have indexed, may have skipped)
  1 — fatal error (config missing, embedder unavailable)
"""

import json
import os
import sys
import time
from pathlib import Path


def _plugin_path(hermes_home: Path) -> Path:
    return hermes_home / "plugins" / "curve-memory"


def _config_path(hermes_home: Path) -> Path:
    return hermes_home / "curve-memory-config.json"


def _load_config(hermes_home: Path) -> dict:
    default = {
        "embedding": {
            "provider": "ollama",
            "model": "qwen3-embedding:8b",
            "base_url": "http://localhost:11434",
        },
    }
    config_path = _config_path(hermes_home)
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            if "embedding" in cfg:
                default["embedding"].update(cfg["embedding"])
        except Exception:
            pass
    # Environment overrides
    for env_key, (section, key) in {
        "CURVE_MEMORY_EMBEDDING_MODEL": ("embedding", "model"),
        "CURVE_MEMORY_EMBEDDING_URL": ("embedding", "base_url"),
    }.items():
        val = os.environ.get(env_key)
        if val:
            default[section][key] = val
    return default


def main():
    hermes_home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    plugin_path = _plugin_path(hermes_home)

    # Add plugin to Python path
    if str(plugin_path) not in sys.path:
        sys.path.insert(0, str(plugin_path))

    # Ensure curve-memory is importable
    try:
        from curve_memory.enrichment import index_sweep
        from curve_memory.core.embedding import create_embedding_provider
    except ImportError as e:
        print(f"FATAL: cannot import curve-memory modules: {e}", file=sys.stderr)
        sys.exit(1)

    memories_dir = hermes_home / "memories"
    if not memories_dir.exists():
        print("OK: memories/ directory does not exist yet, nothing to index")
        sys.exit(0)

    # Create embedder
    cfg = _load_config(hermes_home)
    embedder = create_embedding_provider(cfg["embedding"])
    if embedder is None:
        print("WARN: no embedding backend available (Ollama not running?). Skipping.", file=sys.stderr)
        sys.exit(1)

    # Run sweep
    start = time.time()
    result = index_sweep(memories_dir, embedder)
    elapsed = time.time() - start

    indexed = result.get("indexed", 0)
    cleaned = result.get("cleaned", 0)
    errors = result.get("errors", 0)
    details = result.get("details", [])

    parts = []
    if indexed:
        parts.append(f"indexed {indexed} topics")
    if cleaned:
        parts.append(f"cleaned {cleaned} stale files")
    if errors:
        parts.append(f"{errors} errors")

    if parts:
        print(f"Index sweep: {', '.join(parts)} ({elapsed:.1f}s)")
        if details:
            for d in details:
                if d.get("status") in ("error", "warning"):
                    print(f"  {d['status']}: {d['topic']}: {d.get('message', '')}")
    else:
        print(f"Index sweep: all up to date ({elapsed:.1f}s)")


if __name__ == "__main__":
    main()
