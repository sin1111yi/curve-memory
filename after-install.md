# After Install

## Quick Start

```bash
# 1. Install Ollama models
ollama pull qwen3-embedding:8b        # For semantic search (required)
ollama pull qwen2.5:3b                # For semantic degradation (recommended)

# 2. Configure interactively (model, search weights, archive threshold)
hermes curve-memory config --interactive

# 3. Enable memory provider
hermes config set memory.provider curve-memory
hermes gateway restart

# 4. Build index
hermes curve-memory index --rebuild

# 5. Install nightly cron jobs (optional but recommended)
hermes curve-memory install-cron           # 3 AM semantic degradation

# 6. Verify
hermes curve-memory check
hermes curve-memory status
```

## Step-by-Step

### 1. Install Ollama embedding model

```bash
ollama pull qwen3-embedding:8b
```

### 2. Install semantic degradation model (recommended)

For nightly cron-driven memory summarization (intelligent content condensation instead of blind truncation):

```bash
ollama pull qwen2.5:3b
```

This is a separate model from the embedding model. It's called during the `degrade-semantic` cron job at 3 AM. Without it, degradation falls back to line-based truncation — the system still works, but summaries are less informative.

### 3. Interactive configuration

```bash
hermes curve-memory config --interactive
```

This walks you through 6 settings:
- **model** — Ollama embedding model (default: `qwen3-embedding:8b`)
- **base_url** — Ollama server URL (default: `http://localhost:11434`)
- **search_alpha** — BM25 weight in hybrid search (default: 0.35)
- **search_beta** — Embedding weight in hybrid search (default: 0.45)
- **search_gamma** — Recency weight in hybrid search (default: 0.20)
- **archive_days** — Days before a memory is archived (default: 30)

Config is stored in `~/.hermes/curve-memory-config.json`.

All settings can also be overridden via environment variables:
- `CURVE_MEMORY_EMBEDDING_MODEL`
- `CURVE_MEMORY_EMBEDDING_URL`
- `CURVE_MEMORY_ALPHA`
- `CURVE_MEMORY_BETA`
- `CURVE_MEMORY_GAMMA`
- `CURVE_MEMORY_ARCHIVE_DAYS`

### 4. Enable memory provider

```bash
hermes config set memory.provider curve-memory
hermes gateway restart
```

Use `memory.provider` (not `memory.plugin`).

### 5. Initialize index

```bash
hermes curve-memory index --rebuild
```

This creates:
- `.embedding_index/` — per-topic JSONL vector files
- `.fts5/curve_memory_fts5.db` — SQLite FTS5 full-text index

### 6. Install nightly cron job (recommended)

Install the 3 AM semantic degradation cron job:

```bash
hermes curve-memory install-cron
```

This registers the `degrade-semantic` command in system crontab. Every night at 3:00, it scans memories flagged with `pending_summary: true` and calls `qwen2.5:3b` via Ollama to generate intelligent summaries. During daytime, `degrade_memory()` only sets the pending flag — no content is lost, no latency added.

### 7. Verify installation

```bash
hermes curve-memory check     # Health check (5 items)
hermes curve-memory status    # TIER distribution + index health
```

## First Use

```bash
hermes curve-memory status
hermes curve-memory search "test"
```

### Notes System

Notes are detailed documents stored in `~/.hermes/notes/{name}.md`. They are referenced from memory files via `note: name` lines but NOT loaded into agent context by default.

```bash
# View available notes
hermes curve-memory notes-list

# Read a note
hermes curve-memory notes-show searxng-setup-details

# Notes are created by the agent via the curve_memory_enrich tool
# or manually via the API (write_note + link_note_to_memory)
```

### Semantic Degradation

The system defers content truncation to a nightly batch job for zero-latency daytime operation:

```bash
# Preview what would be processed tonight
hermes curve-memory degrade-semantic --dry-run

# Manually trigger semantic degradation (for testing)
hermes curve-memory degrade-semantic --max-topics 1

# Check cron status
crontab -l | grep degrade-semantic
```

### User Profile

Your user profile (preferences, personal info the agent should know) is stored in `~/.hermes/memories/USER.md`. Edit it directly with natural language, or let the agent manage it via:

- `curve_memory_user_get` — view stored profile
- `curve_memory_user_set` — add/update info
- `curve_memory_user_delete` — remove info

When built-in memory is disabled (`memory.memory_enabled: false`), curve-memory manages both memories and user profile exclusively.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `command not found: hermes curve-memory` | Plugin not installed or not loaded | `hermes plugins install https://github.com/sin1111yi/curve-memory.git && hermes gateway restart` |
| `Ollama connection refused` | Ollama not running | `ollama serve` or check Ollama installation |
| `index --rebuild` fails | Embedding model not found | `ollama pull qwen3-embedding:8b` |
| Search returns 0 results | Index not built | `hermes curve-memory index --rebuild` |
| `memory.provider` not working | Config not set | `hermes config set memory.provider curve-memory && hermes gateway restart` |
| `prefetch` returns 0 chars | Search working but no relevant memories | Search still works — add more memories via `memory add` |
| Response is slow | qwen3-embedding:8b takes ~40ms per chunk | Normal for first-time embedding; subsequent searches are cached |
| `notes-list` returns empty | No notes created yet | Notes are created on demand by the agent — try `curve_memory_enrich` with a note |
