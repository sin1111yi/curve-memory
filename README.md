# curve-memory — 遗忘曲线记忆系统

基于 R(t) = 0.462 + 0.538 · exp(-t/2.71) 遗忘曲线的 Hermes 记忆插件。

## 安装

```bash
hermes plugins install git@github.com:sin1111yi/curve-memory.git
hermes config set memory.plugin curve-memory
```

## 前置依赖

- Ollama: `ollama pull qwen3-embedding:8b`
- Python: `pip install numpy`（用于余弦相似度）

## 核心公式

```
R(t) = 0.462 + 0.538 · exp(-t / 2.71)
R(t) ∈ [0.462, 1.0]，基线 46.2% 永不归零
t = 距离上次访问的天数
```

## TIER 映射

| TIER | R(t) | t | 详细度 |
|------|------|---|--------|
| TIER_5 🔥 | R ≥ 0.800 | ≤ 1天 | 全量 |
| TIER_4 📗 | R ≥ 0.640 | ≤ 3天 | 详细 |
| TIER_3 📙 | R ≥ 0.503 | ≤ 7天 | 摘要 |
| TIER_2 📕 | R ≥ 0.465 | ≤ 14天 | 极简 |
| ARCHIVE 🗄️ | R ≈ 0.462 | ≥ 30天 | 归档 |

## CLI

```bash
curve-memory search "query"     # 三路检索
curve-memory status             # 状态概览
curve-memory touch <topic>      # 置 t=0
curve-memory daily-tick         # 手动衰减
```

## 结构

```
~/.hermes/plugins/curve-memory/
├── plugin.yaml
├── __init__.py          ← 注册入口
└── curve_memory/
    ├── provider.py      ← MemoryProvider
    ├── cli.py           ← CLI
    ├── core/
    │   ├── tier.py      ← R(t) 引擎
    │   ├── search.py    ← 三路检索
    │   ├── activity.py  ← YAML 读写
    │   ├── chunker.py   ← 文本分块
    │   ├── embedding_provider.py
    │   ├── forgetting.py
    │   └── indexer.py
    └── skill/SKILL.md
```
