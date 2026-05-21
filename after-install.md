# After Install

## Quick Start

```bash
# 1. Install Ollama embedding model
ollama pull qwen3-embedding:8b

# 2. Configure interactively (model, search weights, archive threshold)
hermes curve-memory config --interactive

# 3. Enable memory provider
hermes config set memory.provider curve-memory
hermes gateway restart

# 4. Build index
hermes curve-memory index --rebuild

# 5. Verify
hermes curve-memory check
hermes curve-memory status
```

## Step-by-Step

### 1. Install Ollama embedding model

```bash
ollama pull qwen3-embedding:8b
```

### 2. Interactive configuration

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

### 3. Enable memory provider

```bash
hermes config set memory.provider curve-memory
hermes gateway restart
```

Use `memory.provider` (not `memory.plugin`).

### 4. Initialize index

```bash
hermes curve-memory index --rebuild
```

This creates:
- `.embedding_index/` — per-topic JSONL vector files
- `.fts5/curve_memory_fts5.db` — SQLite FTS5 full-text index

### 5. Verify installation

```bash
hermes curve-memory check     # Health check (5 items)
hermes curve-memory status    # TIER distribution + index health
```

## First Use

```bash
hermes curve-memory status
hermes curve-memory search "test"
```

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
