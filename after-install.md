# After Install

## Quick Start

```bash
# 1. Setup cron scripts, dirs, register cron jobs
python3 ~/.hermes/plugins/curve-memory/curve_memory/cli.py setup

# 2. Verify
python3 ~/.hermes/plugins/curve-memory/curve_memory/cli.py check
python3 ~/.hermes/plugins/curve-memory/curve_memory/cli.py status
```

## 1. Install Ollama embedding model

```bash
ollama pull qwen3-embedding:8b
```

## 2. Setup cron scripts + register cron jobs

```bash
python3 ~/.hermes/plugins/curve-memory/curve_memory/cli.py setup
```

This creates:
- `~/.hermes/scripts/curve-memory-forgetting.py` (daily decay at 03:00)
- `~/.hermes/scripts/curve-memory-indexer.py` (daily index at 03:45)

## 3. Enable memory plugin

```bash
hermes config set memory.plugin curve-memory
hermes gateway restart
```

## 4. Initialize index

```bash
python3 ~/.hermes/plugins/curve-memory/curve_memory/cli.py index --rebuild
```

## First Use

```bash
python3 ~/.hermes/plugins/curve-memory/curve_memory/cli.py status
python3 ~/.hermes/plugins/curve-memory/curve_memory/cli.py search "test"
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `cli.py: command not found` | Plugin not installed | `hermes plugins install git@github.com:sin1111yi/curve-memory.git` |
| `Ollama connection refused` | Ollama not running | `ollama serve` or check Ollama installation |
| `index --rebuild` fails | Embedding model not found | `ollama pull qwen3-embedding:8b` |
| Search returns 0 results | Index not built | `python3 ~/.hermes/plugins/curve-memory/curve_memory/cli.py index --rebuild` |
| `memory.plugin` not working | Config not set | `hermes config set memory.plugin curve-memory && hermes gateway restart` |
| `prefetch` returns 0 chars | Search working but no relevant memories | Search still works — add more memories via `memory add` |
| Response is slow | qwen3-embedding:8b takes ~40ms per chunk | Normal for first-time embedding; subsequent searches are cached |
