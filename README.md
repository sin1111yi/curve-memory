
# curve-memory — 遗忘曲线记忆系统 / Forgetting Curve Memory System

[English](#english) | [中文](#中文)

---

<a id="english"></a>

## English

### Overview

**curve-memory** is a [Hermes Agent](https://hermes-agent.nousresearch.com) memory plugin that manages AI memories using a scientifically-grounded forgetting curve. It addresses three fundamental problems with naive linear-activity memory systems:

1. **No gradient** — All memories are equally detailed regardless of age
2. **All-or-nothing archiving** — A memory is either fully retained or suddenly deleted
3. **No knowledge solidification** — Frequently used memories get archived the same as forgotten ones

### The Formula

```math
R(t) = 0.462 + 0.538 · exp(-t / 2.71)
```

| Parameter | Value | Meaning |
|-----------|-------|---------|
| R₀ (baseline) | 0.462 | Core knowledge never fully decays (46.2%) |
| τ (time constant) | 2.71 | Characteristic decay time in days |
| 1 - R₀ | 0.538 | Forgettable portion (short-term component) |

The baseline of 46.2% means that even after months without use, the core summary of a memory remains accessible — it never drops to zero.

### TIER Mapping

| TIER | R(t) threshold | Days | Detail | Behavior |
|------|---------------|------|--------|----------|
| TIER_5 🔥 | R ≥ 0.800 | ≤ 1 | Full detail, all sections | Full load |
| TIER_4 📗 | R ≥ 0.640 | ≤ 3 | Core facts + key details | Detailed load |
| TIER_3 📙 | R ≥ 0.503 | ≤ 7 | Summary, bullet points | Summary load |
| TIER_2 📕 | R ≥ 0.465 | ≤ 14 | One-liner overview | Minimal load |
| TIER_1 📦 | R > 0.462 | 14-30 | Archive pending | Index only |
| ARCHIVE 🗄️ | R ≈ 0.462 | ≥ 30 | Archived | Removed from active |

### Three-Way Hybrid Search

```
Final score = 0.35 · BM25 + 0.45 · Embedding + 0.20 · R(t)
```

| Component | Weight | Source |
|-----------|--------|--------|
| BM25 (keyword) | α = 0.35 | SQLite FTS5 full-text index |
| Embedding (semantic) | β = 0.45 | Ollama qwen3-embedding:8b (4096d) |
| R(t) (freshness) | γ = 0.20 | Forgetting curve from ACTIVITY.yaml |

**Default model:** `qwen3-embedding:8b` (4096 dimensions) via Ollama. The larger dimension provides finer-grained semantic discrimination compared to the typical 384d models.

#### 5-Level Degradation Chain

The system gracefully degrades when components are unavailable:

| Level | Available | What works |
|-------|-----------|------------|
| 0 🟢 | BM25 + Embedding + R(t) | Full three-way search |
| 1 🟡 | BM25 + R(t) only | Keyword + freshness |
| 2 🟡 | Embedding + R(t) only | Semantic + freshness |
| 3 🟠 | R(t) + keyword match | Topic name matching |
| 4 🔴 | Pure idx keyword | Fallback to MEMORY.md index |

### Dual Archive System

#### 1. Forgetting Archive (Natural decay)

```
t ≥ 30 days → mv active/*.md → archive/forgotten/
```

Memories that haven't been used for 30+ days are automatically moved to cold storage. They remain recoverable — if the topic comes up again, the memory is reactivated from `archive/forgotten/`.

#### 2. Mature Archive (Knowledge solidification)

```
access_count ≥ 20 AND t ≤ 3 → mature = true
→ When t ≥ 30: copy to archive/mature/ + promote to ~/.hermes/knowledge/
```

Frequently-used memories are promoted to permanent knowledge documents instead of being forgotten. This prevents the "use it or lose it" problem — the most valuable memories are preserved as durable knowledge.

### Architecture

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
│  forgetting.py        Daily decay cron               │
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

### Data Flow

#### Write Path
```
agent writes active/<topic>.md
  → indexer detects mtime change
  → chunk by H2 sections
  → embed via Ollama qwen3-embedding:8b
  → write .embedding_index/<topic>.jsonl
  → update FTS5 index
```

#### Read Path
```
user message → agent
  → CurveMemoryProvider.prefetch(query)
  → parallel: FTS5 BM25 + Embedding cosine_sim + R(t) lookup
  → normalize + weighted fuse
  → top-3 by TIER level
  → inject into system prompt
```

#### Cron Path (daily at 03:00)
```
curve-memory-forgetting.py:
  → all memories t += 1
  → compute R(t)
  → archive if t ≥ 30 (forgotten or mature)
  → update ACTIVITY.yaml

curve-memory-indexer.py (daily at 03:45):
  → scan active/ for mtime changes
  → re-chunk + re-embed changed files
  → clean stale indexes
```

### Performance Analysis

| Operation | Latency | Notes |
|-----------|---------|-------|
| FTS5 BM25 search | < 5ms | SQLite virtual table |
| Embedding (1 chunk) | ~40ms | Ollama qwen3-embedding:8b |
| Three-way fusion | < 1ms | In-memory dict ops |
| **Total search** | **~50ms** | With all 3 routes |
| Full index rebuild | ~2 min | 13 files, ~40s per file for embedding |
| Incremental index | ~10s | Only changed files |

Estimated index size for 500 memories: < 10 MB (embeddings) + < 5 MB (FTS5).

### Comparison with Alternative Models

| Model | Baseline | t=0 | t=7 | t=30 | t=∞ | Pro/Con |
|-------|----------|-----|-----|------|-----|---------|
| **Linear (act+1)** | 0 | N/A | act=7 | act=30 | N/A | No gradient, hard threshold |
| **Log (log₁.₀₉)** | 0 | ∞ | 22.4 | 5.5 | 0 | Unbounded, subjective base |
| **Ebbinghaus (this)** | **0.462** | **1.0** | **0.503** | **0.462** | **0.462** | Bounded, data-grounded, baseline preserves core |

### Installation

```bash
# Prerequisites
ollama pull qwen3-embedding:8b
pip install numpy

# Install plugin
hermes plugins install git@github.com:sin1111yi/curve-memory.git

# Enable and configure
hermes plugins enable curve-memory
hermes config set memory.plugin curve-memory

# Initialize index
curve-memory index --rebuild

# Restart gateway
hermes gateway restart
```

### CLI Usage

```bash
curve-memory search "borrow checker"    # Three-way search
curve-memory search "R(t) formula" --json  # JSON output
curve-memory status                     # TIER distribution + index health
curve-memory touch rust-lifetimes       # Reset t=0
curve-memory daily-tick                 # Manual decay
curve-memory index --rebuild            # Full reindex
curve-memory forget stale-topic         # Manual archive
curve-memory mature important-topic     # Mark as mature
curve-memory check                      # Health check
```

### Project Structure

```
~/.hermes/plugins/curve-memory/
├── plugin.yaml                  # Plugin metadata
├── __init__.py                  # Registration entry point
├── README.md                    # This file
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

### Design Decisions & Trade-offs

| Decision | Rationale |
|----------|-----------|
| **Ebbinghaus curve vs log** | Data-grounded, bounded [0.462, 1.0], no ∞ special case |
| **Qwen3-8B (4096d) vs MiniLM (384d)** | Finer semantic granularity, better cross-lingual (CN/EN) |
| **Ollama vs sentence-transformers** | Zero Python ML deps, standalone service, multi-model |
| **YAML vs SQLite for activity** | Human-readable, script-friendly, agent-editable |
| **Dual archive vs single** | Frequently-used memories deserve promotion, not deletion |
| **File lock vs DB transactions** | Simple, adequate for single-user cron conflicts |

### Roadmap

- [x] Phase 0: Preparation & backup
- [x] Phase 1: Forgetting curve core (R(t), TIER, cron decay)
- [x] Phase 2: Semantic search (FTS5 + Embedding + R(t) fusion)
- [x] Phase 3: Hermes Plugin packaging & CLI
- [x] Phase 4: Integration & end-to-end verification
- [ ] Phase 5: Long-term tuning (α/β/γ weights, TIER thresholds, maturity params)

---

<a id="中文"></a>

## 中文

### 概述

**curve-memory** 是一个基于 [Hermes Agent](https://hermes-agent.nousresearch.com) 的记忆插件，使用科学遗忘曲线管理 AI 记忆。它解决了传统线性活跃度记忆系统的三个根本问题：

1. **没有梯度** — 所有记忆的详细程度相同，不论多久没用
2. **全有或全无的归档** — 记忆要么完整保留，要么突然消失
3. **没有知识固化** — 频繁使用的记忆和遗忘的记忆一样被归档

### 核心公式

```math
R(t) = 0.462 + 0.538 · exp(-t / 2.71)
```

| 参数 | 值 | 含义 |
|------|-----|------|
| R₀ (基线) | 0.462 | 核心知识永不归零（保留 46.2%） |
| τ (时间常数) | 2.71 | 特征衰减天数 |
| 1 - R₀ | 0.538 | 可遗忘部分（短期记忆成分） |

46.2% 的基线意味着即使数月未使用，记忆的核心摘要仍然可访问——永不归零。

### TIER 映射

| TIER | R(t) 阈值 | 天数 | 详细度 | 行为 |
|------|-----------|------|--------|------|
| TIER_5 🔥 | R ≥ 0.800 | ≤ 1 | 完整详细，全部章节 | 全量加载 |
| TIER_4 📗 | R ≥ 0.640 | ≤ 3 | 核心事实 + 关键细节 | 详细加载 |
| TIER_3 📙 | R ≥ 0.503 | ≤ 7 | 摘要，要点 | 摘要加载 |
| TIER_2 📕 | R ≥ 0.465 | ≤ 14 | 一行概要 | 极简加载 |
| TIER_1 📦 | R > 0.462 | 14-30 | 归档待命 | 仅保留索引 |
| ARCHIVE 🗄️ | R ≈ 0.462 | ≥ 30 | 已归档 | 移出 active |

### 三路混合检索

```
最终得分 = 0.35 · BM25 + 0.45 · 语义嵌入 + 0.20 · R(t)
```

| 分量 | 权重 | 来源 |
|------|------|------|
| BM25（关键词） | α = 0.35 | SQLite FTS5 全文索引 |
| 语义嵌入 | β = 0.45 | Ollama qwen3-embedding:8b (4096维) |
| R(t) 新鲜度 | γ = 0.20 | ACTIVITY.yaml 遗忘曲线 |

**默认模型：** `qwen3-embedding:8b`（4096 维），通过 Ollama 运行。大维度提供了比典型 384 维模型更精细的语义区分能力。

#### 五级降级链

| 级别 | 可用组件 | 工作模式 |
|------|----------|----------|
| 0 🟢 | BM25 + Embedding + R(t) | 三路全开 |
| 1 🟡 | BM25 + R(t) | 关键词 + 新鲜度 |
| 2 🟡 | Embedding + R(t) | 语义 + 新鲜度 |
| 3 🟠 | R(t) + 关键词匹配 | Topic 名称匹配 |
| 4 🔴 | 纯 idx 关键词 | 回退到 MEMORY.md |

### 双层归档

#### 1. 遗忘归档（自然衰减）

```
t ≥ 30 天 → mv active/*.md → archive/forgotten/
```

30 天未使用的记忆自动移至冷存储。可恢复——如果再次提到该主题，从 `archive/forgotten/` 重新激活。

#### 2. 成熟归档（知识固化）

```
access_count ≥ 20 且 t ≤ 3 → mature = true
→ 当 t ≥ 30: 复制到 archive/mature/ + 提升至 ~/.hermes/knowledge/
```

频繁使用的记忆被提升为永久知识文档，而不是被遗忘。这解决了"用进废退"的问题——最有价值的记忆被固化为持久知识。

### 架构

```
┌─ Hermes Agent ──────────────────────────────────────┐
│                                                      │
│  MemoryProvider: curve-memory                        │
│    ├─ prefetch(query) → 三路检索 → TIER 注入         │
│    ├─ sync_turn()    → 自动 touch 提到的主题          │
│    └─ get_tool_schemas() → curve_memory_search 工具   │
│                                                      │
└──────────────────────┬───────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────┐
│  核心引擎 (curve_memory/core/)                        │
│                                                      │
│  tier.py             R(t) 公式 + TIER 映射            │
│  search.py           BM25 + Embedding + R(t) 融合     │
│  activity.py         ACTIVITY.yaml 读写               │
│  chunker.py          Markdown H2 章节分割             │
│  embedding_provider  Ollama qwen3-embedding:8b       │
│  forgetting.py       每日衰减 cron                    │
│  indexer.py          FTS5 + embedding 索引构建器       │
└──────────────────────┬───────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────┐
│  存储层 (~/.hermes/memories/)                         │
│                                                      │
│  ACTIVITY.yaml       t, access_count, mature 标志     │
│  MEMORY.md           idx:topic [t=N] → active/       │
│  active/*.md         活跃记忆文件                      │
│  .embedding_index/   每个 topic 的 JSONL 向量索引      │
│  .fts5/              SQLite FTS5 全文索引             │
│  archive/forgotten/  冷存储（可恢复）                  │
│  archive/mature/     永久知识快照                      │
└──────────────────────────────────────────────────────┘
```

### 数据流

#### 写入路径
```
agent 写入 active/<topic>.md
  → indexer 检测 mtime 变化
  → 按 H2 章节分块
  → 通过 Ollama qwen3-embedding:8b 嵌入
  → 写入 .embedding_index/<topic>.jsonl
  → 更新 FTS5 索引
```

#### 读取路径
```
用户消息 → agent
  → CurveMemoryProvider.prefetch(query)
  → 并行：FTS5 BM25 + Embedding 余弦相似度 + R(t) 查表
  → 归一化 + 加权融合
  → 按 TIER 级别取 top-3
  → 注入 system prompt
```

#### Cron 路径（每天 03:00）
```
curve-memory-forgetting.py：
  → 所有记忆 t += 1
  → 计算 R(t)
  → 若 t ≥ 30 则归档（遗忘或成熟）
  → 更新 ACTIVITY.yaml

curve-memory-indexer.py（每天 03:45）：
  → 扫描 active/ 的 mtime 变化
  → 对变更文件重新分块 + 嵌入
  → 清理过期索引
```

### 性能分析

| 操作 | 延迟 | 说明 |
|------|------|------|
| FTS5 BM25 检索 | < 5ms | SQLite 虚拟表 |
| 嵌入（1 个 chunk） | ~40ms | Ollama qwen3-embedding:8b |
| 三路融合 | < 1ms | 内存字典运算 |
| **总检索** | **~50ms** | 三路全开 |
| 全量索引重建 | ~2 分钟 | 13 个文件 |
| 增量索引 | ~10s | 仅变更文件 |

500 条记忆的预估索引尺寸：< 10 MB（嵌入）+ < 5 MB（FTS5）。

### 与其他模型的对比

| 模型 | 基线 | t=0 | t=7 | t=30 | t=∞ | 优劣 |
|------|------|-----|-----|------|-----|------|
| **线性 (act+1)** | 0 | N/A | act=7 | act=30 | N/A | 无梯度，硬阈值 |
| **对数 (log₁.₀₉)** | 0 | ∞ | 22.4 | 5.5 | 0 | 无边界，底数主观 |
| **艾宾浩斯（本方案）** | **0.462** | **1.0** | **0.503** | **0.462** | **0.462** | 有界，数据驱动，基线保留核心 |

### 安装

```bash
# 前置依赖
ollama pull qwen3-embedding:8b
pip install numpy

# 安装插件
hermes plugins install git@github.com:sin1111yi/curve-memory.git

# 启用并配置
hermes plugins enable curve-memory
hermes config set memory.plugin curve-memory

# 初始化索引
curve-memory index --rebuild

# 重启 gateway
hermes gateway restart
```

### CLI 用法

```bash
curve-memory search "借用检查器"        # 三路检索
curve-memory search "R(t) 公式" --json   # JSON 输出
curve-memory status                      # TIER 分布 + 索引健康
curve-memory touch rust-lifetimes        # 重置 t=0
curve-memory daily-tick                  # 手动衰减
curve-memory index --rebuild             # 全量重建索引
curve-memory forget stale-topic          # 手动归档
curve-memory mature important-topic      # 标记为成熟
curve-memory check                       # 健康检查
```

### 项目结构

```
~/.hermes/plugins/curve-memory/
├── plugin.yaml                  # 插件元数据
├── __init__.py                  # 注册入口
├── README.md                    # 本文件
└── curve_memory/
    ├── __init__.py               # 包标记
    ├── provider.py               # MemoryProvider 实现
    ├── cli.py                    # CLI 工具
    ├── core/
    │   ├── __init__.py
    │   ├── tier.py               # R(t) 引擎 + TIER 映射
    │   ├── search.py             # 三路混合检索
    │   ├── activity.py           # ACTIVITY.yaml 读写
    │   ├── chunker.py            # H2 章节分块
    │   ├── embedding_provider.py # Ollama 嵌入封装
    │   ├── forgetting.py         # 每日衰减 cron 脚本
    │   └── indexer.py            # FTS5 + 嵌入索引构建器
    └── skill/
        └── SKILL.md              # Agent 协议文档
```

### 设计决策与权衡

| 决策 | 理由 |
|------|------|
| **艾宾浩斯曲线 vs 对数** | 数据驱动，有界 [0.462, 1.0]，无 ∞ 特殊值 |
| **Qwen3-8B (4096维) vs MiniLM (384维)** | 更精细语义粒度，更好的跨语言（中/英）能力 |
| **Ollama vs sentence-transformers** | 零 Python ML 依赖，独立服务，多模型支持 |
| **YAML vs SQLite 存储活跃度** | 人类可读，脚本友好，agent 可直接编辑 |
| **双层归档 vs 单层** | 高频使用的记忆应被提升而非删除 |
| **文件锁 vs 数据库事务** | 简单，足够应对单人 cron 冲突 |

### 路线图

- [x] Phase 0: 准备与备份
- [x] Phase 1: 遗忘曲线核心（R(t), TIER, cron 衰减）
- [x] Phase 2: 语义检索（FTS5 + Embedding + R(t) 融合）
- [x] Phase 3: Hermes Plugin 封装与 CLI
- [x] Phase 4: 集成与端到端验证
- [ ] Phase 5: 长期调优（α/β/γ 权重、TIER 阈值、成熟度参数）

---

**License:** MIT
