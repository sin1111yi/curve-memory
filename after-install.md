# After Install

## 1. Install Ollama embedding model

```bash
ollama pull qwen3-embedding:8b
```

## 2. Setup cron scripts

The plugin needs two small symlinks in `~/.hermes/scripts/` for the daily cron jobs:

```bash
curve-memory setup
```

This creates:
- `~/.hermes/scripts/curve-memory-forgetting.py` → plugin's forgetting script
- `~/.hermes/scripts/curve-memory-indexer.py` → plugin's indexer script

## 3. Enable memory plugin

```bash
hermes config set memory.plugin curve-memory
hermes gateway restart
```

## 4. Initialize index

```bash
curve-memory index --rebuild
```

## Verify

```bash
curve-memory check
curve-memory search "test"
```
