# curve-memory — Forgetting Curve Memory System

A [Hermes Agent](https://hermes-agent.nousresearch.com) memory plugin that manages AI memories using a scientifically-grounded forgetting curve.

## Overview

curve-memory addresses three fundamental problems with naive linear-activity memory systems:

1. **No gradient** — All memories are equally detailed regardless of age
2. **All-or-nothing archiving** — A memory is either fully retained or suddenly deleted
3. **No knowledge solidification** — Frequently used memories get archived the same as forgotten ones

## The Formula

```
R(t) = 0.462 + 0.538 · exp(-t / 2.71)
```

| Parameter | Value | Meaning |
|-----------|-------|---------|
| R₀ (baseline) | 0.462 | Core knowledge never fully decays (46.2%) |
| τ (time constant) | 2.71 | Characteristic decay time in days |
| 1 - R₀ | 0.538 | Forgettable portion (short-term component) |

The baseline of 46.2% means that even after months without use, the core summary of a memory remains accessible — it never drops to zero.

### Why This Curve?

The curve is an exponential decay model based on the Ebbinghaus forgetting curve, fitted to empirical data points: R(0)=1.0, R(1)=0.82, R(3)=0.65, R(7)=0.50. The parameter τ=2.71 (approximating Euler's number e) creates a natural decay where R(τ) ≈ 0.660, and the baseline R₀=0.462 preserves core knowledge indefinitely.

**Comparison with alternatives:**

| Model | Baseline | t=0 | t=7 | t=30 | t=∞ | Limitation |
|-------|----------|-----|-----|------|-----|------------|
| Linear (act+1) | 0 | N/A | act=7 | act=30 | N/A | No gradient, hard threshold |
| Log (log₁.₀₉) | 0 | ∞ | 22.4 | 5.5 | 0 | Unbounded, arbitrary base |
| **Ebbinghaus (this)** | **0.462** | **1.0** | **0.503** | **0.462** | **0.462** | Bounded, data-grounded |

## TIER Mapping

| TIER | R(t) threshold | Days | Detail | Behavior |
|------|---------------|------|--------|----------|
| TIER_5 🔥 | R ≥ 0.800 | ≤ 1 | Full detail, all sections | Full load |
| TIER_4 📗 | R ≥ 0.640 | ≤ 3 | Core facts + key details | Detailed load |
| TIER_3 📙 | R ≥ 0.503 | ≤ 7 | Summary, bullet points | Summary load |
| TIER_2 📕 | R ≥ 0.465 | ≤ 14 | One-liner overview | Minimal load |
| TIER_1 📦 | R > 0.462 | 14-30 | Archive pending | Index only |
| ARCHIVE 🗄️ | R ≈ 0.462 | ≥ 30 | Archived | Removed from active |

## Three-Way Hybrid Search

```
Final score = 0.35 · BM25 + 0.45 · Embedding + 0.20 · R(t)
```

| Component | Weight | Source |
|-----------|--------|--------|
| BM25 (keyword) | α = 0.35 | SQLite FTS5 full-text index |
| Embedding (semantic) | β = 0.45 | Ollama qwen3-embedding:8b (4096d) |
| R(t) (freshness) | γ = 0.20 | Forgetting curve from ACTIVITY.yaml |

**Default model:** `qwen3-embedding:8b` (4096 dimensions) via Ollama. The large dimension provides fine-grained semantic discrimination compared to typical 384d models.

### 5-Level Degradation Chain

The system gracefully degrades when components are unavailable:

| Level | Available | What works | Mode |
|-------|-----------|------------|------|
| 0 🟢 | BM25 + Embedding + R(t) | Full three-way search | Best quality |
| 1 🟡 | BM25 + R(t) only | Keyword + freshness | No embedding |
| 2 🟡 | Embedding + R(t) only | Semantic + freshness | No FTS5 |
| 3 🟠 | R(t) + keyword match | Topic name matching | Degraded |
| 4 🔴 | Pure idx keyword | Fallback to MEMORY.md | Last resort |

### Weight Design Rationale

- **α = 0.35 (BM25):** Users often use precise terms like "E0495", "ACTIVITY.yaml", or "R(t)". Exact keyword matching is irreplaceable for these.
- **β = 0.45 (Embedding):** Highest weight — semantic matching covers keyword blind spots like "borrow checker" → "rust-lifetimes". This is the core gain of hybrid search.
- **γ = 0.20 (R(t)):** Floor weight — stale memories shouldn't rank high even with perfect semantic match, but freshness shouldn't overwhelm relevance.

## Dual Archive System

### 1. Forgetting Archive (Natural decay)

```
t ≥ 30 days → mv active/*.md → archive/forgotten/
```

Memories unused for 30+ days move to cold storage. They remain recoverable — if the topic re-emerges, the memory is reactivated from `archive/forgotten/`.

### 2. Mature Archive (Knowledge solidification)

```
access_count ≥ 20 AND t ≤ 3 → mature = true
→ When t reaches 30: copy to archive/mature/ + promote to ~/.hermes/knowledge/
```

Frequently-used memories are promoted to permanent knowledge documents instead of being forgotten. This prevents the "use it or lose it" problem — the most valuable memories become durable knowledge.

## Architecture

```
┌─ Hermes Agent ──────────────────────────────────────┐
│                                                      │
│  MemoryProvider: curve-memory                        │
│    ├─ prefetch(query) → 3-way search → TIER inject   │
│    ├─ sync_turn()    → auto-touch mentioned topics   │
│    └─ get_tool_schemas() → curve_memory_search tool  │
│                                                      │
└──────────────────────┬───────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────┐
│  Core Engine (curve_memory/core/)                    │
│                                                      │
│  tier.py             R(t) formula + TIER mapping     │
│  search.py           BM25 + Embedding + R(t) fusion  │
│  activity.py         ACTIVITY.yaml read/write        │
│  chunker.py          Markdown H2 section splitting   │
│  embedding_provider  Ollama qwen3-embedding:8b       │
│  forgetting.py       Daily decay cron                │
│  indexer.py          FTS5 + embedding index builder  │
└──────────────────────┬───────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────┐
│  Storage (~/.hermes/memories/)                       │
│                                                      │
│  ACTIVITY.yaml       t, access_count, mature flags   │
│  MEMORY.md           idx:topic [t=N] → active/       │
│  active/*.md         Live memory files               │
│  .embedding_index/   Per-topic JSONL vector index    │
│  .fts5/              SQLite FTS5 full-text index     │
│  archive/forgotten/  Cold storage (recoverable)      │
│  archive/mature/     Permanent knowledge snapshots   │
└──────────────────────────────────────────────────────┘
```

## Data Flow

### Write Path
```
agent writes active/<topic>.md
  → indexer detects mtime change
  → chunk by H2 sections
  → embed via Ollama qwen3-embedding:8b
  → write .embedding_index/<topic>.jsonl
  → update FTS5 index
```

### Read Path
```
user message → agent
  → CurveMemoryProvider.prefetch(query)
  → parallel: FTS5 BM25 + Embedding cosine_sim + R(t) lookup
  → normalize + weighted fuse
  → top-3 by TIER level
  → inject into system prompt
```

### Cron Path (daily)
```
03:00 — curve-memory-forgetting.py:
  → all memories t += 1
  → compute R(t), detect maturity
  → archive if t ≥ 30 (forgotten or mature)
  → update ACTIVITY.yaml

03:45 — curve-memory-indexer.py --incremental:
  → scan active/ for mtime changes
  → re-chunk + re-embed changed files
  → clean stale indexes (archived topics)
```

## Performance

| Operation | Latency | Notes |
|-----------|---------|-------|
| FTS5 BM25 search | < 5ms | SQLite virtual table |
| Embedding (1 chunk) | ~40ms | Ollama qwen3-embedding:8b |
| Three-way fusion | < 1ms | In-memory dict ops |
| **Total search** | **~50ms** | All 3 routes active |
| Full index rebuild | ~2 min | 13 files |
| Incremental index | ~10s | Only changed files |

Estimated index size for 500 memories: < 10 MB (embeddings) + < 5 MB (FTS5).

## Installation

### Prerequisites

```bash
# Install Ollama and pull the embedding model
ollama pull qwen3-embedding:8b

# Python dependency (for cosine similarity)
pip install numpy
```

### Plugin Installation

```bash
# 1. Install from GitHub
hermes plugins install https://github.com/sin1111yi/curve-memory.git

# 2. Enable and setup (dirs, cron, interactive config)
hermes plugins enable curve-memory
hermes curve-memory setup

# 3. Enable memory plugin
hermes config set memory.plugin curve-memory

# 4. Build index
hermes curve-memory index --rebuild

# 5. Restart the gateway
hermes gateway restart

# 6. Verify
hermes curve-memory check
```

## CLI Reference

All commands are available as Hermes subcommands:

```bash
hermes curve-memory <command> [args]
```

### Search

```bash
hermes curve-memory search "borrow checker"         # Three-way search
hermes curve-memory search "R(t) formula" --json     # JSON output
```

### System Status

```bash
hermes curve-memory status          # TIER distribution + index health
hermes curve-memory stats           # Detailed statistics
hermes curve-memory config          # View configuration
hermes curve-memory check           # Health check (6 items)
hermes curve-memory plot            # ASCII R(t) curve
```

### Memory Management

```bash
hermes curve-memory touch <topic>         # Reset t=0
hermes curve-memory forget <topic>        # Manual archive
hermes curve-memory mature <topic>        # Mark as mature
hermes curve-memory recover <topic>       # Restore from archive
hermes curve-memory recover --list        # List recoverable topics
hermes curve-memory undo                  # Show recent ops
```

### Index

```bash
hermes curve-memory index --rebuild       # Full reindex
hermes curve-memory index --incremental   # Incremental update
hermes curve-memory repair                # Diagnose issues
hermes curve-memory repair --fix          # Auto-fix
```

### Lifecycle

```bash
hermes curve-memory setup              # Initialize after install
hermes curve-memory install-wizard     # Interactive wizard
hermes curve-memory activate           # Re-enable
hermes curve-memory deactivate         # Disable (preserve data)
hermes curve-memory uninstall [-y]     # Clean up
hermes curve-memory uninstall --all    # Clean up + erase all data
hermes curve-memory export backup.tar.gz  # Export memories

# Daily
hermes curve-memory daily-tick         # Manual decay trigger
```

## Related Projects

- [ralqlator](https://github.com/sin1111yi/ralqlator) — Rust CLI calculator used for real-time R(t) formula verification (`ralqlator "0.462 + 0.538 * pow(C_E, -t / 2.71)"`)

## Project Structure

```
~/.hermes/plugins/curve-memory/
├── plugin.yaml                  # Plugin metadata
├── __init__.py                  # Registration entry point
├── README.md                    # This file (English)
├── README-zh.md                 # Chinese documentation
└── curve_memory/
    ├── __init__.py               # Package marker
    ├── provider.py               # MemoryProvider implementation
    ├── cli.py                    # CLI tool
    ├── core/
    │   ├── __init__.py
    │   ├── tier.py               # R(t) engine + TIER mapping
    │   ├── search.py             # Three-way hybrid search
    │   ├── activity.py           # ACTIVITY.yaml read/write
    │   ├── chunker.py            # H2 section chunking
    │   ├── embedding_provider.py # Ollama embedding wrapper
    │   ├── forgetting.py         # Daily decay cron script
    │   └── indexer.py            # FTS5 + embedding index builder
    └── skill/
        └── SKILL.md              # Agent protocol document
```

## Storage Structure

```
~/.hermes/memories/
├── ACTIVITY.yaml              # t, access_count, mature, protected flags
├── MEMORY.md                  # idx:topic [t=N] → active/topic.md
├── active/                    # Live memory files (13 files)
├── .embedding_index/          # Per-topic JSONL vector index (768-4096d)
├── .fts5/curve_memory_fts5.db # SQLite FTS5 full-text index
├── archive/
│   ├── forgotten/             # Cold storage (recoverable)
│   └── mature/                # Permanent knowledge snapshots
└── FORGET_LOG.md              # Archive event log
```

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| **Ebbinghaus curve over log** | Data-grounded, bounded [0.462, 1.0], no ∞ special case |
| **Qwen3-8B (4096d) over MiniLM (384d)** | Finer semantic granularity, better cross-lingual (CN/EN) support |
| **Ollama over sentence-transformers** | Zero Python ML deps, standalone service, multi-model support |
| **YAML over SQLite for activity** | Human-readable, script-friendly, agent-editable |
| **Dual archive over single** | Frequently-used memories deserve promotion, not deletion |
| **File lock over DB transactions** | Simple, adequate for single-user cron conflicts |
| **Importlib for hyphenated scripts** | Maintains backward compat with ~/.hermes/scripts/ usage |

## Roadmap

- [x] Phase 0: Preparation & backup
- [x] Phase 1: Forgetting curve core (R(t), TIER, cron decay)
- [x] Phase 2: Semantic search (FTS5 + Embedding + R(t) fusion)
- [x] Phase 3: Hermes Plugin packaging & CLI
- [x] Phase 4: Integration & end-to-end verification
- [ ] Phase 5: Long-term tuning (α/β/γ weights, TIER thresholds, maturity params)

## License

MIT
- [ralqlator](tools/ralqlator) — 命令行计算器，用于 R(t) 公式验证和数学计算
