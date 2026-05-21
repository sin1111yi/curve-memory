# After Install

## Quick Start

```bash
# 1. One-step setup: create dirs, register cron jobs, interactive config
hermes curve-memory setup

# 2. Verify
hermes curve-memory check
hermes curve-memory status
```

## 1. Install Ollama embedding model

```bash
ollama pull qwen3-embedding:8b
```

## 2. Setup + configure

```bash
hermes curve-memory setup
```

This walks you through:
- Directory creation (`~/.hermes/memories/active`, `archive/`, etc.)
- Cron script deployment (`~/.hermes/scripts/curve-memory-forgetting.py` at 03:00, `curve-memory-indexer.py` at 03:45)
- Interactive configuration (embedding model, search weights, archive thresholds)

## 3. Enable memory plugin

```bash
hermes config set memory.provider curve-memory
hermes gateway restart
```

## 4. Initialize index

```bash
hermes curve-memory index --rebuild
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
