# curve-memory — Forgetting Curve Memory System

> **⚠️ Alpha — 测试阶段。本插件接管 Hermes 的记忆系统和用户画像，当前版本可能包含未发现的问题，包括但不限于数据丢失、双系统冲突、配置失效等。请先在测试环境中验证，确认后再在正式环境使用。**
>
> **⚠️ Alpha — testing phase. This plugin takes over Hermes' memory system and user profile. The current version may contain undiscovered issues including but not limited to data loss, dual-system conflicts, and configuration failures. Validate in a test environment before using in production.**

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
│    ├─ get_tool_schemas() → curve_memory_search tool  │
│    └─ get_config_schema() / save_config()            │
│                                                      │
└──────────────────────┬───────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────┐
│  Core Engine (curve_memory/core/)                    │
│                                                      │
│  tier.py        R(t) formula + TIER mapping          │
│  search.py      BM25 + Embedding + R(t) fusion       │
│  activity.py    ACTIVITY.yaml read/write             │
│  embedding.py   ABC EmbeddingProvider + factory       │
│  config.py      get_config_schema, load/save config   │
│                                                      │
│  agent/provider.py    MemoryProvider implementation            │
│  core/                R(t), search, activity, embedding, config │
│  backends/            Ollama embedding client                   │
│  enrichment.py        degrade, archive, enrich, index_sweep     │
└──────────────────────┬──────────────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────────────┐
│  Hermes-managed (~/.hermes/)                                    │
│                                                                    │
│  memories/           Memory + profile + embeddings + FTS5       │
│  cron/jobs.json      Index sweep cron (auto-registered)         │
│  scripts/curve-memory-index-sweep.py  Standalone cron entry     │
│  curve-memory-config.json   Plugin configuration                │
```

## Data Flow

### Write Path

```python
agent writes active/<topic>.md
  → index_sweep() (lazy on initialize, or daily cron at 03:00):
    → check .jsonl mtime vs .md mtime
    → chunk content (≤2000 chars per chunk)
    → embed via Ollama qwen3-embedding:8b (/api/embed, 60s timeout)
    → write .embedding_index/<topic>.jsonl
  → (FTS5 index: online, updated by sync_turn() during conversation)
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

### Archive Sweep (lazy, at initialize/on_session_end)
```
initialize() / on_session_end():
  → scan active memories
  → compute R(t) from Unix timestamps (no day-counter)
  → detect maturity (access_count ≥ 20 AND t ≤ 3 days)
  → archive if t ≥ archive_threshold_days
    → mature → copy to archive/mature/ + promote to knowledge/
    → forgotten → move to archive/forgotten/
  → update ACTIVITY.yaml
```

### Index Sweep Cron (daily 3:00 AM)

```cron
initialize():
  → if embedder available: run index_sweep() (lazy, catches up immediately)
  → register a daily no_agent cron (idempotent, skipped if already registered)

Daily at 03:00 (via Hermes cron scheduler):
  → script: ~/.hermes/scripts/curve-memory-index-sweep.py
  → for each active topic: check .jsonl mtime vs .md mtime
    → missing or stale → chunk content (≤2000 chars) → embed → write .jsonl
  → remove orphaned .jsonl files (topics no longer in ACTIVITY.yaml)
```

| Item | Detail |
|------|--------|
| Schedule | Daily at 03:00 (configurable via `jobs.json`) |
| Mode | `no_agent` (pure script, no LLM involved) |
| Runtime | ~1 min for 10 topics, ~10s incremental |
| Script location | `~/.hermes/scripts/curve-memory-index-sweep.py` |
| Auto-registration | `_register_index_cron()` in `initialize()` — one-time, idempotent |
| Cleanup | `shutdown()` removes stale jobs and scripts |

The cron ensures embedding indexes are always fresh — new memories written during conversation are automatically indexed within 24 hours, even without manual `hermes curve-memory index` commands.

## Config

Stored in `{hermes_home}/curve-memory-config.json` (JSON format, not config.yaml).

| Key | Description | Default |
|-----|-------------|---------|
| `model` | Ollama embedding model name | `qwen3-embedding:8b` |
| `base_url` | Ollama server URL | `http://localhost:11434` |
| `search_alpha` | BM25 weight (0-1) | `0.35` |
| `search_beta` | Embedding weight (0-1) | `0.45` |
| `search_gamma` | Recency weight (0-1) | `0.20` |
| `archive_days` | Days before archiving (0 = never) | `30` |

Configure via:
```bash
hermes curve-memory config --interactive
```

Or via environment variables:
- `CURVE_MEMORY_EMBEDDING_MODEL`
- `CURVE_MEMORY_EMBEDDING_URL`
- `CURVE_MEMORY_ALPHA`
- `CURVE_MEMORY_BETA`
- `CURVE_MEMORY_GAMMA`
- `CURVE_MEMORY_ARCHIVE_DAYS`

## Managed Components

curve-memory fully takes over two systems:

| Component | Built-in (default) | curve-memory |
|-----------|-------------------|--------------|
| **Memories** | `MEMORY.md` — flat key-value store with `idx:` index | `active/*.md` — one file per topic, forgetting curve (R(t)), hybrid search |
| **User Profile** | `USER.md` — managed by Hermes `memory` tool | `USER.md` (in `memories/`) — natural language, injected via `system_prompt_block()` |

The plugin provides 4 tools: `curve_memory_search` (memory recall), `curve_memory_user_get/set/delete` (profile management).

## Migration from Built-in Memory

If you already have Hermes built-in memory data, migrate as follows:

### 1. Memories (MEMORY.md)

The `idx:` index format is already compatible — no migration needed. Your existing `active/*.md` files are recognized immediately.

### 2. User Profile (USER.md)

```bash
# Export existing user profile from built-in memory
hermes memory get > ~/user-profile-backup.txt

# The agent can then import entries via curve_memory_user_set,
# or you can edit ~/.hermes/memories/USER.md directly.
```

> **Note:** `MEMORY.md` itself is NOT migrated to `active/*.md` — it's only the index (`idx:`). The actual memory content was already written to `active/*.md` by the agent through the memory index protocol. If you have key-value entries in `MEMORY.md` that aren't indexed, the agent will still read them from the built-in system until you disable it.

### 3. Disable Built-in Memory (optional, after migration)

Once curve-memory is configured and verified:

```bash
hermes config set memory.memory_enabled false
hermes config set memory.user_profile_enabled false
hermes gateway restart
```

This removes the built-in `memory` tool and `USER.md` from the agent's system prompt.
curve-memory's tools and prompt blocks become the exclusive source.

## Performance

| Operation | Latency | Notes |
|-----------|---------|-------|
| FTS5 BM25 search | < 5ms | SQLite virtual table |
| Embedding (1 chunk) | ~40ms | Ollama qwen3-embedding:8b |
| Three-way fusion | < 1ms | In-memory dict ops |
| **Total search** | **~50ms** | All 3 routes active |
| Full index rebuild | ~1 min per 10 files | Depends on topic count |
| Incremental index | ~10s | Only changed files |

Estimated index size for 500 memories: < 10 MB (embeddings) + < 5 MB (FTS5).

## Installation

### Prerequisites

```bash
# Install Ollama and pull the embedding model
ollama pull qwen3-embedding:8b
```

### Plugin Installation

```bash
# 1. Install from GitHub
hermes plugins install https://github.com/sin1111yi/curve-memory.git

# 2. Enable the plugin
hermes plugins enable curve-memory

# 3. Interactive configuration (model, search weights, archive threshold)
hermes curve-memory config --interactive

# 4. Enable memory provider
hermes config set memory.provider curve-memory

# 5. Rebuild the index
hermes curve-memory index --rebuild

# 6. Restart the gateway
hermes gateway restart

# 7. Verify
hermes curve-memory check
hermes curve-memory status
```

## CLI Reference

All commands are available as Hermes subcommands:

```bash
hermes curve-memory <command> [args]
```

### 12 Commands

| Command | Description | Flags |
|---------|-------------|-------|
| `search <query>` | Three-way hybrid search | `--json`, `--top-k N` |
| `status` | System status + TIER distribution | — |
| `config` | View or configure | `--interactive` (config wizard) |
| `check` | Health check (5 items) | — |
| `activate` | Re-enable curve-memory provider | — |
| `deactivate` | Disable (preserve data) | — |
| `index` | Build/rebuild index | `--rebuild` (full rebuild) |
| `notes-list` | List all available notes | — |
| `notes-show <name>` | View a note's content | — |
| `notes-delete <name>` | Delete a note | — |
| `degrade-semantic` | Nighttime semantic degradation | `--dry-run`, `--max-topics N` |
| `install-cron` | Install 3 AM cron job for degrade | — |

### Search

```bash
hermes curve-memory search "borrow checker"         # Three-way search
hermes curve-memory search "R(t) formula" --json     # JSON output
```

### System Status

```bash
hermes curve-memory status          # TIER distribution + index health
hermes curve-memory config          # View configuration
hermes curve-memory check           # Health check (5 items)
```

### Configuration

```bash
hermes curve-memory config                          # View current config
hermes curve-memory config --interactive            # Interactive config wizard
```

### Activation

```bash
hermes curve-memory activate         # Re-enable (sets memory.provider)
hermes curve-memory deactivate       # Disable (preserves all data)
```

### Index

```bash
hermes curve-memory index            # Incremental index update
hermes curve-memory index --rebuild  # Full rebuild from scratch
```

### Notes System

```bash
hermes curve-memory notes-list                      # List all notes
hermes curve-memory notes-show searxng-setup-details # View note content
hermes curve-memory notes-delete searxng-setup-details # Delete a note
```

Notes are stored in `~/.hermes/notes/{name}.md` and referenced from memory files via a `note: name` line. They are NOT loaded into agent context by default — use `curve_memory_read_note` tool to fetch them on demand.

### Semantic Degradation (Cron-Driven)

```bash
hermes curve-memory degrade-semantic             # Process all memories exceeding TIER targets
hermes curve-memory degrade-semantic --dry-run    # Preview what would be processed
hermes curve-memory degrade-semantic --max-topics 1  # Process only 1 topic

# Install nightly cron job (3 AM)
hermes curve-memory install-cron
```

The system defers content truncation to a nightly batch job:

```
Daytime (conversation)                    Nighttime 3 AM (cron)
────────────────────                      ────────────────────
sync_turn() → _touch_memory()            curve memory degrade-semantic
  → update timestamp (ISO 8601)            → scan ALL active memories
  → NO TIER detection                     → compute R(t) from timestamps
  → NO file truncation                    → check content vs TIER target
  → NO Ollama calls                       → if content exceeds target:
  ~0ms overhead                              ├─ has note? → drop Details, keep Summary + note:
                                             └─ no note? → qwen2.5:3b condenses Details only
                                             **Summary** is never touched
```

**Memory file format:**
```
## topic-name
**Summary**: <agent-maintained one-line summary — preserved at all TIER levels>

**Details**:
<detailed content, condenses via Ollama when TIER drops>

## Enriched (conversation)
note: reference-to-external-note
```

| Condition | Action | Cost |
|-----------|--------|------|
| Has `note:` reference | Drop **Details**, keep **Summary** + note ref | ~0ms (no model) |
| No note, Details too large | Condense **Details** via qwen2.5:3b | 20-60s per topic (nighttime) |
| Already within TIER target | Skip | ~0ms |

**Safety**: If Ollama is offline during cron, the topic is skipped and retried next night. No data loss — content remains in full until successfully condensed.

**ISO 8601 timestamps**: ACTIVITY.yaml stores human-readable timestamps (via `date -Iseconds`):
```yaml
searxng:
  t: 2026-05-26T01:50:15+08:00
  access_count: 73
```
`parse_timestamp()` handles both ISO strings and legacy Unix integers transparently.

## Related Projects

- [ralqlator](https://github.com/sin1111yi/ralqlator) — Rust CLI calculator used for real-time R(t) formula verification (`ralqlator "0.462 + 0.538 * pow(C_E, -t / 2.71)"`)

## Project Structure

```
~/.hermes/plugins/curve-memory/
├── plugin.yaml                  # Plugin metadata
├── __init__.py                  # Registration entry point
├── README.md                    # This file (English)
├── README-zh.md                 # Chinese documentation
├── after-install.md             # Post-install guide
├── scripts/
│   └── curve-memory-index-sweep.py   # Standalone cron entry point
└── curve_memory/
    ├── __init__.py               # Package marker
    ├── provider.py               # MemoryProvider implementation
    ├── cli.py                    # CLI tool (7 commands)
    ├── enrichment.py             # degrade, archive, enrich, index_sweep
    ├── core/
    │   ├── __init__.py
    │   ├── tier.py               # R(t) engine + TIER mapping
    │   ├── search.py             # Three-way hybrid search
    │   ├── activity.py           # ACTIVITY.yaml read/write
    │   ├── embedding.py          # ABC EmbeddingProvider + factory
    │   └── config.py             # Config schema, load/save config
    ├── backends/
    │   ├── __init__.py
    │   └── ollama.py             # Ollama embedding client (/api/embed)
    └─ skill/
        └── SKILL.md              # Agent protocol document
    └── curve_memory/core/note.py  # Notes system (CRUD + reference detection) [NEW]
    └── curve_memory/backends/generate.py  # Ollama generate backend [NEW]
```

## Storage Structure

```
~/.hermes/memories/
├── ACTIVITY.yaml              # t (timestamp), access_count, mature, protected flags
├── MEMORY.md                  # idx:topic [t=N] → active/topic.md
├── active/                    # Live memory files (*.md)
├── .embedding_index/          # Per-topic JSONL vector index (*.jsonl)
├── .fts5/curve_memory_fts5.db # SQLite FTS5 full-text index
├── archive/
│   ├── forgotten/             # Cold storage (recoverable)
│   └── mature/                # Permanent knowledge snapshots
├── USER.md                    # User profile (natural language + ## Auto)
└── .tier_cache.json           # Cached TIER levels (for detect_tier_drops)

~/.hermes/
├── scripts/curve-memory-index-sweep.py   # Cron script (auto-copied)
├── cron/jobs.json                        # Cron registry (auto-managed)
├── curve-memory-config.json              # Plugin configuration
└── notes/                                # Notes system [NEW]
    └── *.md                              # On-demand notes referenced from memories
```

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| **Ebbinghaus curve over log** | Data-grounded, bounded [0.462, 1.0], no ∞ special case |
| **Qwen3-8B (4096d) over MiniLM (384d)** | Finer semantic granularity, better cross-lingual (CN/EN) support |
| **Ollama over sentence-transformers** | Zero Python ML deps, standalone service, multi-model support |
| **YAML over SQLite for activity** | Human-readable, script-friendly, agent-editable |
| **Dual archive over single** | Frequently-used memories deserve promotion, not deletion |
| **Timestamp-based R(t) over cron** | No cron dependency, computed at query time from Unix timestamps |
| **Lazy archive sweep over daily cron** | Archive happens on initialize/end-session, not via scheduled scripts |
| **Cron-driven index sweep over online indexing** | Embedding computation runs daily at 03:00 via no_agent script; new memories indexed within 24h without blocking conversation |
| **JSON config over YAML section** | Self-contained, compatible with `get_config_schema()` / `save_config()` |
| **`memory.provider` over `memory.plugin`** | Standard ABC MemoryProvider interface |
| **Notes as separate files** | Detailed content stored in `~/.hermes/notes/` — not in agent context unless explicitly fetched |
| **Cron-driven semantic degradation** | Content truncation moved to a nightly 3 AM batch job using qwen2.5:3b for intelligent summarization — zero user-facing latency |

## MemoryProvider Implementation

The plugin implements the full `MemoryProvider` abstract base class:

| Method | Purpose |
|--------|---------|
| `initialize()` | Create resources, load config, init embedder, run lazy archive sweep |
| `prefetch(query)` | Called before each turn — injects up to 3 relevant memories |
| `sync_turn(user, asst)` | Called after each turn — updates activity for mentioned topics |
| `system_prompt_block()` | Short description of the memory system |
| `get_tool_schemas()` | Returns 4 tools in OpenAI function-calling format (see below) |
| `handle_tool_call()` | Executes tool calls for all 4 tools |
| `get_config_schema()` | Config schema for `hermes memory setup` |
| `save_config(values, hermes_home)` | Save config from schema values |
| `on_session_end(messages)` | Lazy archive sweep on session end |
| `shutdown()` | Clean up resources |

## Tools

The plugin exposes 4 tools the agent can call:

| Tool | Purpose | Key parameters |
|------|---------|----------------|
| `curve_memory_search` | Hybrid search across persistent memories | `query` (str), `top_k` (int, default 5) |
| `curve_memory_user_get` | Get all stored user profile entries | — |
| `curve_memory_user_set` | Store a user fact (persists across sessions) | `key` (str), `value` (str) |
| `curve_memory_user_delete` | Remove a user fact | `key` (str) |
| `curve_memory_read_note` | Load a detailed note on demand (NOT in context by default) | `note_name` (str) |

### User Profile

User profile data is stored in `{hermes_home}/memories/USER.md` as natural language text. It's injected into the system prompt via `system_prompt_block()` and can be queried via `prefetch()` (matched against query keywords).

The file has two sections:
- **Manual section** (top): free-form natural language — edit directly
- **## Auto section** (bottom): tool-generated entries — managed by `curve_memory_user_set`/`delete`

## Roadmap

- [x] Phase 0: Preparation & backup
- [x] Phase 1: Forgetting curve core (R(t), TIER, decay)
- [x] Phase 2: Semantic search (FTS5 + Embedding + R(t) fusion)
- [x] Phase 3: Hermes Plugin packaging & CLI
- [x] Phase 4: Integration & end-to-end verification
- [x] Phase 5: MemoryProvider ABC refactoring
- [x] Phase 6: Notes System — detailed notes stored separately, loaded on demand
- [x] Phase 7: Cron-driven semantic degradation — qwen2.5:3b summarization at 3 AM
- [ ] Phase 8: Long-term tuning (α/β/γ weights, TIER thresholds, maturity params)

## License

MIT
