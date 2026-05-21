# Curve-Memory Plugin Architecture — Refactor Plan

**Date:** 2026-05-21  
**Author:** Hermes Agent (architecture analysis)  
**Status:** Design / Ready for Implementation  
**Repo:** `~/.hermes/plugins/curve-memory/`

---

## Table of Contents

1. [Current State Assessment](#1-current-state-assessment)
2. [Target Architecture Overview](#2-target-architecture-overview)
3. [Directory Structure](#3-directory-structure)
4. [File-by-File Specification](#4-file-by-file-specification)
5. [CLI Specification](#5-cli-specification)
6. [Config Schema Specification](#6-config-schema-specification)
7. [Data Flow](#7-data-flow)
8. [Migration Path for Legacy Files](#8-migration-path-for-legacy-files)
9. [Implementation Order](#9-implementation-order)

---

## 1. Current State Assessment

### What works

- `hermes curve-memory --help` shows 20 subcommands (too many)
- `hermes curve-memory setup` creates dirs + cron + config wizard
- `hermes curve-memory check` runs health checks
- `get_config_schema()` returns 6 fields, provider discoverable
- `HybridSearch` works: BM25 + embedding cosine + R(t) fusion
- `OllamaBackend` works as embedding provider
- `ACTIVITY.yaml` parser/writer works
- `R(t)` forgetting curve math is correct

### What needs fixing

| # | Problem | Severity |
|---|---------|----------|
| 1 | **CLI bloat**: 20 subcommands, many redundant | High |
| 2 | **Cron scripts**: forgetting.py + indexer.py deployed as cron jobs | High |
| 3 | **Fragile imports**: 11 `sys.path.insert(0, ...)` calls | High |
| 4 | **Config duality**: writes both JSON (`curve-memory-config.json`) and edits `config.yaml` | Medium |
| 5 | **Forgetting model**: uses day-counter `t` incremented by cron instead of real timestamps | Medium |
| 6 | **Hardcoded paths**: some files still use `Path.home() / ".hermes"` instead of `hermes_home` | Medium |
| 7 | **Dead files**: `activity_log.py` only used by removed CLI commands | Low |
| 8 | **Lock file**: `forgetting.py` and `indexer.py` use lock files for cron safety (removed with cron) | Low |

### Import fragility count

```
__init__.py:                      sys.path.insert(0, ...)  (1)
cli.py (shim):                    sys.path.insert(0, ...)  (1)
curve_memory/cli.py:              sys.path.insert(0, ...)  (3)
curve_memory/core/search.py:      sys.path.insert(0, ...)  (2)
curve_memory/core/indexer.py:     sys.path.insert(0, ...)  (2)
curve_memory/core/forgetting.py:  sys.path.insert(0, ...)  (2)
Total: 11
```

All eliminated in the refactor — these modules are part of a proper package and use `from curve_memory.core.xxx import yyy`.

---

## 2. Target Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Hermes Agent                                  │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │                    MemoryManager                                │  │
│  │  initialize() → prefetch() → sync_turn() → shutdown()          │  │
│  └────────────────────────┬───────────────────────────────────────┘  │
│                           │  ctx.register_memory_provider()          │
│                           ▼                                          │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │               CurveMemoryProvider                               │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │  │
│  │  │ Config   │  │Embedding │  │  Search  │  │  Activity     │  │  │
│  │  │ (JSON)   │  │(Ollama)  │  │ (Hybrid) │  │  (YAML I/O)   │  │  │
│  │  └──────────┘  └──────────┘  └──────────┘  └───────────────┘  │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                           │                                          │
│                           ▼                                          │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │               CLI: hermes curve-memory <subcommand>             │  │
│  │  search | status | config | check | activate | deactivate |    │  │
│  │  index [--rebuild]                                             │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                           │                                          │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │               On-Disk Layout (hermes_home/)                     │  │
│  │  memories/active/*.md          — Memory documents              │  │
│  │  memories/ACTIVITY.yaml        — t, access_count, mature       │  │
│  │  memories/.embedding_index/    — Per-topic embedding vectors   │  │
│  │  memories/.fts5/               — FTS5 BM25 SQLite index        │  │
│  │  memories/archive/forgotten/   — Archived (forgotten) docs     │  │
│  │  memories/archive/mature/      — Archived (mature) docs        │  │
│  │  curve-memory-config.json      — Plugin config (non-secret)    │  │
│  └────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

### Component Interaction Diagram

```
Startup:
  HermesAgent.__init__()
    └─ discover_memory_providers()
         └─ curve-memory/__init__.py::register(ctx)
              └─ ctx.register_memory_provider(CurveMemoryProvider())
  MemoryManager.initialize_all(session_id, hermes_home=...)
    └─ CurveMemoryProvider.initialize(session_id, hermes_home=...)
         ├─ load_config(hermes_home)           → reads JSON
         ├─ create_embedding_provider(config)  → OllamaBackend
         └─ HybridSearch(memories_dir, ...)    → searcher ready

Each Turn:
  MemoryManager
    ├─ prefetch(user_message)                  → returns context string
    │    └─ HybridSearch.search(query, top_k=3)
    │         ├─ BM25 (FTS5 SQLite)
    │         ├─ Embedding cosine (JSONL index)
    │         └─ R(t) recency (ACTIVITY.yaml)
    └─ sync_turn(user, assistant)              → updates ACTIVITY.yaml
         ├─ _extract_mentioned_topics()
         └─ _touch_memory(topic, memories_dir) → t=0, access_count++

Lazy Archiving:
  initialize() | prefetch() | on_session_end()
    └─ sweep: for each memory, if should_archive(t):
         ├─ forget_archive() → move to archive/forgotten/
         └─ or mature_archive() → copy to archive/mature/ + knowledge/

Shutdown:
  MemoryManager.shutdown_all()
    └─ CurveMemoryProvider.shutdown() → clear references
```

---

## 3. Directory Structure

### Before (current)

```
~/.hermes/plugins/curve-memory/
├── __init__.py
├── plugin.yaml
├── cli.py                          # shim
├── curve_memory/
│   ├── __init__.py
│   ├── provider.py                 # CurveMemoryProvider
│   ├── cli.py                      # 1000 lines, 20 subcommands
│   ├── backends/
│   │   ├── __init__.py
│   │   └── ollama.py
│   └── core/
│       ├── __init__.py
│       ├── config.py
│       ├── embedding_provider.py   # ABC + factory + cosine_similarity
│       ├── search.py
│       ├── tier.py
│       ├── activity.py
│       ├── forgetting.py           # CRON — TO DELETE
│       ├── indexer.py              # CRON — TO DELETE
│       ├── activity_log.py         # DEAD — TO DELETE
│       └── chunker.py              # KEEP (used by index CLI)
```

### After (target)

```
~/.hermes/plugins/curve-memory/
├── __init__.py                     # register(ctx) — clean, no sys.path.insert
├── plugin.yaml                     # hooks: [on_session_end]
├── cli.py                          # shim re-exporting register_cli (keep, Hermes requires it)
├── README.md                       # (update existing)
├── .hermes/plans/                  # architecture docs
│   └── arch-curve-memory-refactor.md
└── curve_memory/
    ├── __init__.py                 # package marker
    ├── provider.py                 # CurveMemoryProvider (refactored)
    ├── cli.py                      # ~300 lines, 7 subcommands
    ├── backends/
    │   ├── __init__.py             # package marker
    │   └── ollama.py               # OllamaBackend (unchanged logic)
    └── core/
        ├── __init__.py             # package marker
        ├── config.py               # config schema, load, save
        ├── embedding.py            # renamed from embedding_provider.py
        ├── search.py               # HybridSearch (clean imports)
        ├── tier.py                 # forgetting_curve, TIER mapping (unchanged)
        ├── activity.py             # ACTIVITY.yaml I/O (unchanged)
        └── chunker.py              # Markdown chunking (unchanged, kept for index)
```

### Files created / renamed

- `curve_memory/core/embedding.py` — **new**, renamed from `embedding_provider.py`
- `README.md` — update existing

### Files to delete

- `curve_memory/core/forgetting.py` — cron script, logic absorbed into provider
- `curve_memory/core/indexer.py` — cron script, logic absorbed into CLI `index` command
- `curve_memory/core/activity_log.py` — dead code, only used by removed CLI commands
- `curve_memory/core/embedding_provider.py` — renamed to `embedding.py`

### Files unchanged (logic kept, imports cleaned)

- `curve_memory/backends/ollama.py` — only change: from relative to absolute imports if needed
- `curve_memory/core/tier.py` — no changes needed
- `curve_memory/core/activity.py` — no changes needed (except fixing `load_activity()` to accept hermes_home)
- `curve_memory/core/chunker.py` — no changes needed

---

## 4. File-by-File Specification

### 4.1 `__init__.py` (root)

**Purpose:** Plugin entry point — registers the memory provider with Hermes.

**Public API:**
```python
def register(ctx) -> None
def get_provider() -> Optional[CurveMemoryProvider]
```

**Changes from current:**
- Remove `sys.path.insert(0, ...)` — not needed for proper package
- Remove `sys` import, remove `_PLUGIN_DIR` logic
- Keep `register(ctx)` and `get_provider()` signatures identical

**Imports:**
```python
from curve_memory.provider import CurveMemoryProvider
```

---

### 4.2 `plugin.yaml`

**Purpose:** Plugin manifest.

**Content (unchanged):**
```yaml
name: curve-memory
version: 1.0.0
description: "Forgetting-curve memory system — R(t) + hybrid search"
author: Snowlyn
hooks:
  - on_session_end
```

**No changes needed.** The `hooks: [on_session_end]` is correct — the provider implements `on_session_end()` for lazy archiving.

---

### 4.3 `cli.py` (shim, root level)

**Purpose:** Hermes' `discover_plugin_cli_commands()` looks for `<plugin_dir>/cli.py` with `register_cli(subparser)`. This shim re-exports from `curve_memory/cli.py`.

**Changes from current:**
- Remove `sys.path.insert(0, ...)` — not needed when installed as package
- Remove `sys` and `Path` imports
- Keep the `from curve_memory.cli import register_cli` line

---

### 4.4 `curve_memory/__init__.py`

**Purpose:** Package marker. Minimal.

**Content:**
```python
"""Curve-memory memory provider package."""
```

**No changes needed.**

---

### 4.5 `curve_memory/provider.py`

**Purpose:** `CurveMemoryProvider` — full `MemoryProvider` ABC implementation. This is the main business logic.

**Public API:**
```python
class CurveMemoryProvider(MemoryProvider):
    name = "curve-memory"

    def __init__(self)
    def is_available(self) -> bool
    def initialize(self, session_id: str, **kwargs) -> None
    def get_config_schema(self) -> List[Dict[str, Any]]
    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None
    def system_prompt_block(self) -> str
    def prefetch(self, query: str, *, session_id: str = "") -> str
    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None
    def get_tool_schemas(self) -> List[Dict[str, Any]]
    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str
    def on_session_end(self, messages: List[Dict[str, Any]]) -> None
    def shutdown(self) -> None
```

**Key changes from current:**

1. **Lazy archiving in `on_session_end()`**: Instead of cron-based forgetting.py, scan all memories in ACTIVITY.yaml and archive any that exceed `archive_threshold_days`. This runs at session boundaries.

2. **Lazy archiving in `initialize()`**: Also sweep on startup in case the system was down for many days.

3. **`_touch_memory` stores timestamp not day-counter**: The `t` field in ACTIVITY.yaml changes from a day-counter to a Unix timestamp of `last_access_time`. `R(t)` is computed as `forgetting_curve((now - last_access) / 86400)`. This eliminates the need for cron entirely — no more daily t += 1.

   **Migration**: On first initialize(), convert existing `t` (day-counter) values by computing `last_access_time = now - t * 86400`.

4. **`_extract_mentioned_topics` unchanged**: Still regex-matches topic names in text.

5. **Remove `try/except ImportError` guard around class definition**: The class should always be importable. If `agent.memory_provider` is missing, the plugin simply won't activate — the error is caught at a higher level.

6. **Remove `__import__` in `handle_tool_call`**: Use a proper import at the top of the file for `r_to_tier_name`.

**Imports:**
```python
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider

from curve_memory.core.config import get_config_schema, load_config, schema_values_to_config, save_config
from curve_memory.core.embedding import create_embedding_provider
from curve_memory.core.search import HybridSearch
from curve_memory.core.activity import parse_activity, format_activity
from curve_memory.core.tier import forgetting_curve, r_to_tier_name
```

---

### 4.6 `curve_memory/cli.py`

**Purpose:** CLI subcommand registration and handlers. Drastically reduced from 1000 lines to ~300 lines.

**Public API:**
```python
def register_cli(subparser) -> None
```

**Subcommands kept (7):**
1. `search <query>` — hybrid search
2. `status` — system status overview
3. `config [-i/--interactive]` — view/edit config
4. `check` — health check
5. `activate` — set `memory.provider` to curve-memory
6. `deactivate` — unset `memory.provider`
7. `index [--rebuild]` — rebuild/incremental indexing

**Subcommands removed (13):**
- `plot` — novelty, not operational
- `daily-tick` — obsolete (no cron-based forgetting)
- `install-wizard` — redundant with `check` + `config --interactive`
- `export` — out of scope for CLI, use filesystem tools
- `stats` — folded into `status`
- `undo` — only existed for cron-based ops, no longer needed
- `repair` — `check` already reports issues
- `recover` — edge case, filesystem cp works
- `setup` — folded into `config --interactive` (the only setup needed is config)
- `uninstall` — use `hermes plugins remove curve-memory`
- `touch` — internal action, not a user-facing CLI command
- `forget` — manual archive trigger, edge case
- `mature` — manual mature trigger, edge case

**Detailed command specs:**

#### `search <query>`
```
hermes curve-memory search <query> [--top-k N] [--json]
```
- Loads embedder and searcher from config
- Prints formatted results with TIER, score, R(t), snippet
- `--json` for machine-readable output
- Hardcodes no paths — uses `load_config()` from provider

#### `status`
```
hermes curve-memory status
```
- Active memory count, archived counts
- TIER distribution (bar chart)
- Embedder health (name, dim, connected)
- Index status (embedding + FTS5)
- Config summary

#### `config`
```
hermes curve-memory config [-i/--interactive]
```
- Without `-i`: prints current config from JSON file
- With `-i`: interactive wizard that writes to JSON file
- Does NOT edit `config.yaml` directly — uses `hermes_home/curve-memory-config.json`

#### `check`
```
hermes curve-memory check
```
- ACTIVITY.yaml format + version
- Directory structure (active, archive, knowledge)
- Embedder connectivity test (pings Ollama)
- Index integrity check
- No more script file checks (removed check for deleted scripts)

#### `activate` / `deactivate`
```
hermes curve-memory activate
hermes curve-memory deactivate
```
- Unchanged from current — runs `hermes config set/unset memory.provider`

#### `index`
```
hermes curve-memory index [--rebuild]
```
- Without `--rebuild`: incremental (only changed files)
- With `--rebuild`: full rebuild of embedding + FTS5
- Uses chunker.py for tier-based chunk granularity
- No cron, no lock file, no standalone entry point

**Imports:**
```python
import argparse
import json
import logging
from pathlib import Path
from typing import Any

from curve_memory.core.config import load_config, save_config, get_config_schema, schema_values_to_config
from curve_memory.core.embedding import create_embedding_provider
from curve_memory.core.search import HybridSearch
from curve_memory.core.activity import parse_activity, format_activity, load_activity
from curve_memory.core.tier import forgetting_curve, r_to_tier_name
from curve_memory.core.chunker import chunk_tier_summary, chunk_file
```

---

### 4.7 `curve_memory/backends/ollama.py`

**Purpose:** Ollama embedding backend — communicates with Ollama API to produce embeddings.

**Public API:**
```python
class OllamaBackend(EmbeddingProvider):
    def __init__(self, model: str = "nomic-embed-text", base_url: str = "http://localhost:11434")
    def embed(self, text: str) -> List[float]
    def embed_batch(self, texts: List[str]) -> List[List[float]]
    @property
    def dim(self) -> int
    @property
    def name(self) -> str
```

**Changes from current:** None — the implementation is solid. Only change: verify import path works with renamed `embedding.py` module.

**Current import:**
```python
from ..core.embedding_provider import EmbeddingProvider
```
→ Change to:
```python
from ..core.embedding import EmbeddingProvider
```

---

### 4.8 `curve_memory/core/config.py`

**Purpose:** Configuration schema, loading, saving. The single source of truth for config.

**Public API:**
```python
def get_config_path(hermes_home: str = "") -> Path
def load_config(hermes_home: str = "") -> dict
def save_config(values: dict, hermes_home: str = "") -> None
def get_config_schema() -> list
def schema_values_to_config(values: dict) -> dict
def format_config(cfg: dict) -> str
```

**Changes from current:**

1. **Fix `load_activity()` hardcoded path**: The `load_activity()` function in activity.py hardcodes `Path.home() / ".hermes" / "memories"`. Change to accept `memories_dir` parameter or derive from `hermes_home`.

2. **Remove `_interactive_config` from cli.py**: The interactive config wizard that edits `config.yaml` directly is wrong. The correct approach is to write to `curve-memory-config.json`. The config CLI command should use `get_config_schema()` + `save_config()` just like `hermes memory setup` does.

3. **Keep JSON storage**: `curve-memory-config.json` in `hermes_home` is the right approach per Hermes patterns. The JSON file is the native config location for this provider.

**Imports (unchanged):**
```python
import os
import json
from pathlib import Path
from typing import Any, Dict, Optional
```

---

### 4.9 `curve_memory/core/embedding.py` (renamed from `embedding_provider.py`)

**Purpose:** Embedding provider ABC + factory function + utility functions.

**Reason for rename:** Shorter, cleaner name. The `_provider` suffix was redundant since it lives in `core/`.

**Public API:**
```python
class EmbeddingProvider(ABC):
    def embed(self, text: str) -> List[float]
    def embed_batch(self, texts: List[str]) -> List[List[float]]
    @property
    def dim(self) -> int
    @property
    def name(self) -> str

def cosine_similarity(a: List[float], b: List[float]) -> float
def create_embedding_provider(config: dict = None) -> Optional[EmbeddingProvider]
def load_embedding_index(embedding_dir: Path) -> dict
```

**Changes from current:**
- Move `load_embedding_index()` from `search.py` into `embedding.py` (it's an embedding utility, not search logic)
- Update import path in `ollama.py`

**Imports:**
```python
from abc import ABC, abstractmethod
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
```

---

### 4.10 `curve_memory/core/search.py`

**Purpose:** HybridSearch — BM25 + embedding cosine + R(t) fusion.

**Public API:**
```python
class HybridSearch:
    def __init__(self, memories_dir: Path, embedder=None,
                 alpha: float = 0.35, beta: float = 0.45, gamma: float = 0.20)
    def search(self, query: str, top_k: int = 5,
               alpha: float = None, beta: float = None, gamma: float = None)
               -> List[Tuple[str, float, str, float]]
    @property
    def degrade_info(self) -> str
```

**Changes from current:**
1. **Remove `sys.path.insert` lines 16 and 207** — use proper package imports
2. **Remove `load_embedding_index()`** — move to `embedding.py`
3. **Fix `_load_activity()`** — accept `memories_dir` instead of doing `sys.path.insert` to find scripts

**Imports (cleaned):**
```python
import json
import math
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from curve_memory.core.tier import forgetting_curve, r_to_tier_name, r_to_tier_level
from curve_memory.core.activity import parse_activity
from curve_memory.core.embedding import cosine_similarity, load_embedding_index
```

---

### 4.11 `curve_memory/core/tier.py`

**Purpose:** Forgetting curve math and TIER mappings.

**Public API:**
```python
def forgetting_curve(t: float) -> float
def r_to_tier_name(r: float) -> str
def r_to_tier_level(r: float) -> int
def r_to_tier_abbr(tier_name: str) -> str
def t_to_tier_name(t: int) -> str
def should_archive(t: int) -> bool
def is_mature(access_count: int, t: int) -> bool
```

**Changes from current:** None — the math and mapping are correct. The constants can remain as module-level variables.

---

### 4.12 `curve_memory/core/activity.py`

**Purpose:** ACTIVITY.yaml parser and writer.

**Public API:**
```python
def parse_activity(text: str) -> dict
def format_activity(data: dict) -> str
def load_activity(memories_dir: Optional[Path] = None) -> dict
```

**Changes from current:**
- Fix `load_activity()` to accept `memories_dir` parameter instead of hardcoding `Path.home() / ".hermes" / "memories"`
- Default `memories_dir=None` for backward compatibility, but the provider always passes a path

**Imports (unchanged):**
```python
import re
from pathlib import Path
from typing import Optional
```

---

### 4.13 `curve_memory/core/chunker.py`

**Purpose:** Markdown H2 section splitting for tier-aware indexing.

**Public API:**
```python
def chunk_markdown(topic: str, content: str) -> List[Dict]
def chunk_file(filepath: Path, topic: str = None) -> List[Dict]
def chunk_tier_summary(topic: str, content: str, tier_level: int) -> List[Dict]
```

**No changes needed.** Keep as-is. Used by the `index` CLI command.

---

## 5. CLI Specification

### 5.1 Command Tree

```
hermes curve-memory
├── search <query>
│   ├── --top-k N    (int, default=5)
│   └── --json       (flag, JSON output)
├── status
├── config
│   └── -i, --interactive  (flag, interactive wizard)
├── check
├── activate
├── deactivate
└── index
    └── --rebuild    (flag, full rebuild)
```

### 5.2 Command Details

#### `search`

```
hermes curve-memory search "machine learning concepts" --top-k 10 --json
```

- Loads config from `hermes_home/curve-memory-config.json`
- Creates embedder (OllamaBackend) and HybridSearch
- Searches with alpha/beta/gamma from config
- Default output: formatted table with TIER, score, R(t), snippet
- `--json`: machine-readable JSON array

#### `status`

```
hermes curve-memory status
```

Sample output:
```
=== Curve Memory Status ===

📁 Active memories: 47
📦 Archived (forgotten): 12
🎓 Archived (mature): 5

📊 TIER Distribution:
  TIER_5 🔥     : 15 ███████████████
  TIER_4 📗     : 12 ████████████
  TIER_3 📙     : 10 ██████████
  TIER_2 📕     : 6  ██████
  TIER_1 📦     : 4  ████
  ARCHIVE 🗄️   : 2  ██

🔎 Embedding: qwen3-embedding:8b (dim=1024) ✅
🔎 BM25 (FTS5): ✅
🔎 Embedding index: 47 files, 128 KB
```

#### `config`

```
hermes curve-memory config [--interactive]
```

**Non-interactive:** reads and displays `hermes_home/curve-memory-config.json`

**Interactive:** uses `get_config_schema()` fields to prompt, then calls `save_config()`

Unlike the current implementation, this does NOT edit `config.yaml`. It writes to the provider's native JSON file only.

#### `check`

```
hermes curve-memory check
```

- [1/5] ACTIVITY.yaml: exists, valid format
- [2/5] Directory structure: active, archive/forgotten, archive/mature, knowledge
- [3/5] Embedder connectivity: tests Ollama endpoint
- [4/5] Index integrity: embedding files valid JSONL, FTS5 queryable
- [5/5] No more cron script checks

#### `activate` / `deactivate`

Unchanged. Runs `hermes config set/unset memory.provider curve-memory`.

#### `index`

```
hermes curve-memory index [--rebuild]
```

Implements the indexing logic from the deleted `indexer.py`:
- Full rebuild: clears and regenerates all embedding JSONL + FTS5
- Incremental (default): checks mtime cache, only processes changed files
- Tier-aware chunking via `chunk_tier_summary()`
- No lock file, no cron integration

---

## 6. Config Schema Specification

### 6.1 Schema Fields (`get_config_schema()`)

| Key | Description | Default | Secret | Required | Choices |
|-----|-------------|---------|--------|----------|---------|
| `model` | Ollama embedding model name | `qwen3-embedding:8b` | No | No | — |
| `base_url` | Ollama server URL | `http://localhost:11434` | No | No | — |
| `search_alpha` | BM25 weight (0-1) | `0.35` | No | No | — |
| `search_beta` | Embedding weight (0-1) | `0.45` | No | No | — |
| `search_gamma` | Recency weight (0-1) | `0.20` | No | No | — |
| `archive_days` | Days before archiving | `30` | No | No | — |

**These 6 fields are unchanged from the current implementation.** They are minimal and correct per the Hermes convention (only prompt for essential fields; advanced options go in the config file).

### 6.2 Internal Config Structure (`load_config()` returns)

```python
{
    "embedding": {
        "provider": "ollama",           # fixed
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
    },
}
```

### 6.3 Config File Location

`{hermes_home}/curve-memory-config.json`

This is the native config file pattern recommended by the Hermes memory provider guide. The `save_config()` method writes here. The `get_config_schema()` values are converted via `schema_values_to_config()` and saved as JSON.

### 6.4 Environment Variable Overrides

| Env Var | Config Path |
|---------|-------------|
| `CURVE_MEMORY_EMBEDDING_MODEL` | `embedding.model` |
| `CURVE_MEMORY_EMBEDDING_URL` | `embedding.base_url` |
| `CURVE_MEMORY_ALPHA` | `search.alpha` |
| `CURVE_MEMORY_BETA` | `search.beta` |
| `CURVE_MEMORY_GAMMA` | `search.gamma` |
| `CURVE_MEMORY_ARCHIVE_DAYS` | `tier.archive_threshold_days` |

These overrides are applied in `load_config()` after reading the JSON file.

---

## 7. Data Flow

### 7.1 Startup Sequence

```
Hermes CLI startup
  └─ main()
       └─ discover_memory_providers()
            └─ import curve-memory/__init__.py
                 └─ register(ctx)
                      └─ ctx.register_memory_provider(CurveMemoryProvider())
                           └─ MemoryManager._provider = provider

MemoryManager.initialize_all(<session_id>, hermes_home=..., platform=...)
  └─ provider.initialize(session_id, hermes_home=str(home_path))
       ├─ self._base = Path(kwargs["hermes_home"])
       ├─ self._memories_dir = self._base / "memories"
       ├─ self._cfg = load_config(kwargs["hermes_home"])
       │    ├─ Read {hermes_home}/curve-memory-config.json
       │    └─ Apply env var overrides
       ├─ self._embedder = create_embedding_provider(self._cfg["embedding"])
       │    └─ OllamaBackend(model, base_url) + ping test
       ├─ self._searcher = HybridSearch(self._memories_dir, embedder, alpha, beta, gamma)
       │    └─ Detects degrade level (0-4)
       ├─ Lazy archive sweep:
       │    ├─ Read ACTIVITY.yaml
       │    ├─ For each memory with t >= archive_threshold_days:
       │    │    ├─ If mature: mature_archive()
       │    │    └─ Else: forget_archive()
       │    └─ Write updated ACTIVITY.yaml
       └─ self._touched_topics = set()
```

### 7.2 Per-Turn Flow

```
User sends message
  └─ AIAgent.run_conversation(user_message)
       ├─ MemoryManager.prefetch(user_message)
       │    └─ provider.prefetch(query=user_message)
       │         ├─ if no searcher or empty query → return ""
       │         ├─ results = self._searcher.search(query, top_k=3)
       │         │    ├─ _load_activity() → {topic: {t, access_count, mature}}
       │         │    ├─ Compute R(t) = forgetting_curve(t) for each topic
       │         │    ├─ BM25 scores from FTS5 (if available)
       │         │    ├─ Embedding cosine scores from JSONL (if available)
       │         │    ├─ Fusion: score = α·bm25 + β·cosine + γ·recency
       │         │    └─ Return top_k sorted [(topic, score, snippet, r)]
       │         ├─ Format as "## Retrieved Memories\n### topic (TIER)\nsnippet"
       │         ├─ Add topic to self._touched_topics
       │         └─ Return formatted string → injected into system/user message
       │
       ├─ LLM processes message + context → generates response + tool calls
       │
       ├─ Tool calls handled (may include curve_memory_search)
       │    └─ provider.handle_tool_call("curve_memory_search", args)
       │         ├─ args: {query, top_k}
       │         ├─ results = self._searcher.search(query, top_k)
       │         └─ Return JSON: {results: [{topic, score, snippet, tier}, ...]}
       │
       └─ MemoryManager.sync_turn(user_message, assistant_response)
            └─ provider.sync_turn(user, asst)
                 ├─ mentioned = _extract_mentioned_topics(user)
                 ├─ mentioned += _extract_mentioned_topics(asst)
                 ├─ for topic in (mentioned ∪ self._touched_topics):
                 │    └─ _touch_memory(topic, memories_dir)
                 │         ├─ Read ACTIVITY.yaml
                 │         ├─ Set topic.t = current_timestamp (seconds since epoch)
                 │         ├─ Increment topic.access_count
                 │         └─ Write ACTIVITY.yaml
                 └─ self._touched_topics.clear()
```

### 7.3 Session End Flow

```
AIAgent.run_conversation() completes / user exits
  └─ MemoryManager.on_session_end(messages)
       └─ provider.on_session_end(messages)
            ├─ Lazy archive sweep (same as initialize):
            │    ├─ Read ACTIVITY.yaml
            │    ├─ For each memory:
            │    │    ├─ t_days = (now - last_access_timestamp) / 86400
            │    │    ├─ If t_days >= archive_threshold_days:
            │    │    │    ├─ If mature: mature_archive()
            │    │    │    └─ Else: forget_archive()
            │    └─ Write updated ACTIVITY.yaml
            └─ (No further action needed)
```

### 7.4 Shutdown Flow

```
Hermes exits
  └─ MemoryManager.shutdown_all()
       └─ provider.shutdown()
            ├─ self._searcher = None
            ├─ self._embedder = None
            └─ self._cfg = {}
```

---

## 8. Migration Path for Legacy Files

### 8.1 `curve_memory/core/forgetting.py` — DELETE

**Current usage:** Cron script deployed to `~/.hermes/scripts/curve-memory-forgetting.py` via `setup` command. Runs daily at 3 AM. Called from `cmd_daily_tick` and `cmd_forget` in CLI.

**Replacement:**
- **Forgetting logic**: Absorbed into provider. `_touch_memory()` stores `last_access_time` as Unix timestamp instead of incrementing a day-counter.
- **Archiving**: Done lazily in `initialize()` and `on_session_end()`.
- **`R(t)` computation**: Done on-the-fly in `search.py` by computing `t_days = (now - last_access) / 86400`.
- **Cron jobs**: Remove during `config --interactive` or a one-time migration step.

**Migration step for existing users:**
1. On first `initialize()` after upgrade, convert all `t` values in ACTIVITY.yaml from day-counters to timestamps: `t = int(time.time()) - t * 86400`.
2. Remove cron jobs (`snowlyn-memory-decay`, `snowlyn-memory-index`) from `~/.hermes/cron/jobs.json`.
3. Delete `~/.hermes/scripts/curve-memory-forgetting.py`.

### 8.2 `curve_memory/core/indexer.py` — DELETE

**Current usage:** Cron script deployed to `~/.hermes/scripts/curve-memory-indexer.py`. Runs daily at 3:45 AM. Called from `cmd_index` in CLI.

**Replacement:**
- **Indexing logic**: Kept but moved into the `index` CLI command handler (in `cli.py`).
- **No cron**: Indexing is triggered manually via `hermes curve-memory index` or `--rebuild`.
- **No lock file**: The lock file was only needed for cron safety (preventing concurrent runs).

**Migration step for existing users:**
1. Delete `~/.hermes/scripts/curve-memory-indexer.py`.
2. Index will be rebuilt on next `hermes curve-memory index --rebuild`.

### 8.3 `curve_memory/core/activity_log.py` — DELETE

**Current usage:** Provides `log_operation()`, `get_recent_ops()`, `get_op_stats()` used only by `cmd_undo` and `cmd_stats` in CLI — both of which are being removed.

**Rationale:** The operation log was used for the `undo` feature, which was only useful when cron scripts made automated changes. With lazy archiving, there are no batch mutations to undo. The user can still manually touch memories if needed.

**No migration needed.** The `.activity_log.jsonl` file can be safely deleted.

### 8.4 `curve_memory/core/embedding_provider.py` → Rename to `embedding.py`

**Reason:** Shorter name, consistent with other core modules (`config.py`, `search.py`, `tier.py`, etc.).

**Migration:** Update the import in `ollama.py` from `..core.embedding_provider` to `..core.embedding`.

### 8.5 `sys.path.insert(0, ...)` calls — REMOVE ALL (11 occurrences)

All removed. The plugin is a proper Python package. Imports use relative or absolute package paths.

### 8.6 Hardcoded `Path.home() / ".hermes"` — FIX

Files with hardcoded paths:
- `curve_memory/core/activity.py:97` — `load_activity()` defaults to `~/.hermes/memories/ACTIVITY.yaml`
- `curve_memory/core/activity_log.py:12` — `LOG_FILE = Path.home() / ".hermes" / "memories" / ".activity_log.jsonl"` (file being deleted)
- `curve_memory/core/forgetting.py:39` — `MEMORIES_DIR` (file being deleted)
- `curve_memory/core/indexer.py:37` — `MEMORIES_DIR` (file being deleted)

**Fix:** `activity.py::load_activity()` should accept `memories_dir: Optional[Path] = None` parameter. All callers (provider, search) pass the correct path from `hermes_home`.

### 8.7 Cron Jobs Removal

On upgrade, the `config --interactive` command should offer to clean up old cron jobs. Alternatively, the provider's `initialize()` can check for and remove stale cron jobs on first start.

---

## 9. Implementation Order

### Phase 1: Foundation (imports + package structure)

**Step 1:** Remove all `sys.path.insert()` calls
- Files: `__init__.py`, `cli.py` (shim), `cli.py` (curve_memory), `search.py`
- Action: Replace with proper package imports
- Test: `python3 -c "from curve_memory.provider import CurveMemoryProvider"`

**Step 2:** Rename `embedding_provider.py` → `embedding.py`
- Action: `git mv embedding_provider.py embedding.py`
- Action: Update imports in `ollama.py`, `search.py`, `provider.py`
- Test: `python3 -c "from curve_memory.core.embedding import EmbeddingProvider, create_embedding_provider"`

**Step 3:** Fix `load_activity()` to accept `memories_dir` parameter
- File: `activity.py`
- Action: Add `memories_dir` parameter, remove hardcoded default path
- Action: Update callers in `provider.py` and `search.py`
- Test: Verify ACTIVITY.yaml reads work with paths

### Phase 2: Core Logic (timestamp-based forgetting)

**Step 4:** Change `_touch_memory()` to store Unix timestamps
- File: `provider.py`
- Action: Change `topic["t"] = 0` → `topic["t"] = int(time.time())`
- Action: Add migration code in `initialize()` to convert existing `t` values
  - Old format: `t` = days since last access (integer)
  - New format: `t` = Unix timestamp of last access (integer)
  - Migration: `if t < 1000000000000: t = int(time.time()) - t * 86400`

**Step 5:** Add lazy archiving to provider
- File: `provider.py`
- Action: Implement `_archive_sweep()` method
- Action: Call it in `initialize()` and `on_session_end()`
- Action: Use `should_archive(t_days)` and `forgetting_curve(t_days)` with real time delta

**Step 6:** Add migration + cron cleanup in `initialize()`
- File: `provider.py`
- Action: On first run after upgrade, remove old cron jobs from `~/.hermes/cron/jobs.json`
- Action: On first run after upgrade, delete `~/.hermes/scripts/curve-memory-forgetting.py` and `curve-memory-indexer.py`

### Phase 3: CLI Reduction

**Step 7:** Rewrite `curve_memory/cli.py`
- Action: Remove 13 subcommands, keep 7
- Action: Rewrite `cmd_search`, `cmd_status`, `cmd_config`, `cmd_check`, `cmd_activate`, `cmd_deactivate`
- Action: Implement `cmd_index` with logic from deleted `indexer.py`
- Action: Interactive config writes to JSON file, not `config.yaml`

**Step 8:** Update `cli.py` shim
- File: `cli.py` (root level)
- Action: Remove `sys.path.insert`, keep the re-export

### Phase 4: Cleanup

**Step 9:** Delete legacy files
- `curve_memory/core/forgetting.py`
- `curve_memory/core/indexer.py`
- `curve_memory/core/activity_log.py`

**Step 10:** Update `plugin.yaml` if needed (likely unchanged)

**Step 11:** Update README.md with new CLI and architecture

### Phase 5: Testing

**Step 12:** Test core lifecycle
- `initialize()` with hermes_home → loads config, creates embedder, searcher
- `prefetch()` with query → returns context
- `sync_turn()` with user/asst → updates ACTIVITY.yaml
- `on_session_end()` → archives old memories
- `shutdown()` → clean teardown

**Step 13:** Test CLI subcommands
- `hermes curve-memory search <query> [--json]`
- `hermes curve-memory status`
- `hermes curve-memory config [-i]`
- `hermes curve-memory check`
- `hermes curve-memory index [--rebuild]`

**Step 14:** Test migration path
- Create ACTIVITY.yaml with old-format `t` values
- Run provider.initialize() → verify conversion
- Verify old cron jobs removed
- Verify old scripts deleted

---

## Summary of Key Decisions

| Decision | Rationale |
|----------|-----------|
| **Timestamp-based forgetting** | Eliminates cron dependency. R(t) computed from real time delta at query time. More accurate. |
| **Lazy archiving in initialize() + on_session_end()** | No cron needed. Archiving happens at natural lifecycle boundaries. |
| **7 CLI subcommands (down from 20)** | KISS. Most removed commands were one-off utilities, novelty features, or redundant with config. |
| **JSON config file (not config.yaml)** | Per Hermes convention — provider writes to native config location. `config.yaml` editing was fragile. |
| **`embedding.py` rename (from `embedding_provider.py`)** | Consistent naming with other core modules. Shorter. |
| **Keep chunker.py** | Still used by index CLI command for tier-aware embedding chunking. |
| **Delete activity_log.py** | Only used by now-removed undo/stats commands. Dead code. |
| **Delete cron scripts but keep indexing logic in CLI** | Indexing is still useful as a manual operation. Just not as a cron job. |
