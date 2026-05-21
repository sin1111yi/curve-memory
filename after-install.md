# After Install

## Quick Start

```bash
# 1. 一键初始化
curve-memory setup

# 2. 验证
curve-memory check
curve-memory status
```

## 1. Install Ollama embedding model

```bash
ollama pull qwen3-embedding:8b
```

## 2. Setup cron scripts + register cron jobs

```bash
curve-memory setup
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
curve-memory index --rebuild
```

## First Use

```bash
curve-memory status              # View system status
curve-memory search "test"       # Try a search
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `curve-memory: command not found` | Plugin not installed | `hermes plugins install git@github.com:sin1111yi/curve-memory.git` |
| `Ollama connection refused` | Ollama not running | `ollama serve` or check Ollama installation |
| `index --rebuild` fails | Embedding model not found | `ollama pull qwen3-embedding:8b` |
| Search returns 0 results | Index not built | `curve-memory index --rebuild` |
| `memory.plugin` not working | Config not set | `hermes config set memory.plugin curve-memory && hermes gateway restart` |
| `prefetch` returns 0 chars | Search working but no relevant memories | Search still works — add more memories via `memory add` |
| Response is slow | qwen3-embedding:8b takes ~40ms per chunk | Normal for first-time embedding; subsequent searches are cached |
