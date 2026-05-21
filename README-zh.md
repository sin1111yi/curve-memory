# curve-memory — 遗忘曲线记忆系统

基于 [Hermes Agent](https://hermes-agent.nousresearch.com) 的记忆插件，使用科学遗忘曲线管理 AI 记忆。

## 概述

curve-memory 解决了传统线性活跃度记忆系统的三个根本问题：

1. **没有梯度** — 所有记忆的详细程度相同，不论多久没用
2. **全有或全无的归档** — 记忆要么完整保留，要么突然消失
3. **没有知识固化** — 频繁使用的记忆和遗忘的记忆一样被归档

## 核心公式

```
R(t) = 0.462 + 0.538 · exp(-t / 2.71)
```

| 参数 | 值 | 含义 |
|------|-----|------|
| R₀ (基线) | 0.462 | 核心知识永不归零（保留 46.2%） |
| τ (时间常数) | 2.71 | 特征衰减天数 |
| 1 - R₀ | 0.538 | 可遗忘部分（短期记忆成分） |

46.2% 的基线意味着即使数月未使用，记忆的核心摘要仍然可访问——永不归零。

### 为什么选这条曲线

该曲线是基于艾宾浩斯遗忘曲线的指数衰减模型，拟合自经验数据：R(0)=1.0, R(1)=0.82, R(3)=0.65, R(7)=0.50。参数 τ=2.71（近似自然常数 e）使得 R(τ) ≈ 0.660，基线 R₀=0.462 永久保留核心知识。

**与替代方案的对比：**

| 模型 | 基线 | t=0 | t=7 | t=30 | t=∞ | 局限性 |
|------|------|-----|-----|------|-----|--------|
| 线性 (act+1) | 0 | N/A | act=7 | act=30 | N/A | 无梯度，硬阈值 |
| 对数 (log₁.₀₉) | 0 | ∞ | 22.4 | 5.5 | 0 | 无边界，底数主观 |
| **艾宾浩斯（本方案）** | **0.462** | **1.0** | **0.503** | **0.462** | **0.462** | 有界，数据驱动 |

## TIER 映射

| TIER | R(t) 阈值 | 天数 | 详细度 | 行为 |
|------|-----------|------|--------|------|
| TIER_5 🔥 | R ≥ 0.800 | ≤ 1 | 完整详细，全部章节 | 全量加载 |
| TIER_4 📗 | R ≥ 0.640 | ≤ 3 | 核心事实 + 关键细节 | 详细加载 |
| TIER_3 📙 | R ≥ 0.503 | ≤ 7 | 摘要，要点 | 摘要加载 |
| TIER_2 📕 | R ≥ 0.465 | ≤ 14 | 一行概要 | 极简加载 |
| TIER_1 📦 | R > 0.462 | 14-30 | 归档待命 | 仅保留索引 |
| ARCHIVE 🗄️ | R ≈ 0.462 | ≥ 30 | 已归档 | 移出 active |

## 三路混合检索

```
最终得分 = 0.35 · BM25 + 0.45 · 语义嵌入 + 0.20 · R(t)
```

| 分量 | 权重 | 来源 |
|------|------|------|
| BM25（关键词） | α = 0.35 | SQLite FTS5 全文索引 |
| 语义嵌入 | β = 0.45 | Ollama qwen3-embedding:8b (4096维) |
| R(t) 新鲜度 | γ = 0.20 | ACTIVITY.yaml 遗忘曲线 |

**默认模型：** `qwen3-embedding:8b`（4096 维），通过 Ollama 运行。大维度提供了比典型 384 维模型更精细的语义区分能力。

### 权重设计理由

- **α = 0.35 (BM25)：** 用户常使用精确术语如"E0495"、"ACTIVITY.yaml"、"R(t)"，关键词精确匹配不可替代。
- **β = 0.45 (Embedding)：** 最高权重——语义匹配覆盖关键词盲区，如"借用检查器"→"rust-lifetimes"。这是混合检索的核心增益。
- **γ = 0.20 (R(t))：** 保底权重——即使语义完全匹配，过时的记忆也不应冲顶；但新鲜度不应压倒相关性。

### 五级降级链

当组件不可用时，系统优雅降级：

| 级别 | 可用组件 | 工作模式 |
|------|----------|----------|
| 0 🟢 | BM25 + Embedding + R(t) | 三路全开，最佳质量 |
| 1 🟡 | BM25 + R(t) | 关键词 + 新鲜度，无语义 |
| 2 🟡 | Embedding + R(t) | 语义 + 新鲜度，无全文索引 |
| 3 🟠 | R(t) + 关键词匹配 | Topic 名称匹配，降级模式 |
| 4 🔴 | 纯 idx 关键词 | 回退到 MEMORY.md 索引 |

## 双层归档

### 1. 遗忘归档（自然衰减）

```
t ≥ 30 天 → mv active/*.md → archive/forgotten/
```

30 天未使用的记忆自动移至冷存储。可恢复——如果再次提到该主题，从 `archive/forgotten/` 重新激活。

### 2. 成熟归档（知识固化）

```
访问次数 ≥ 20 且 t ≤ 3 → mature = true
→ 当 t 达到 30: 复制到 archive/mature/ + 提升至 ~/.hermes/knowledge/
```

频繁使用的记忆被提升为永久知识文档，而不是被遗忘。这解决了"用进废退"的问题——最有价值的记忆被固化为持久知识。

## 架构

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

## 数据流

### 写入路径
```
agent 写入 active/<topic>.md
  → indexer 检测 mtime 变化
  → 按 H2 章节分块
  → 通过 Ollama qwen3-embedding:8b 嵌入
  → 写入 .embedding_index/<topic>.jsonl
  → 更新 FTS5 索引
```

### 读取路径
```
用户消息 → agent
  → CurveMemoryProvider.prefetch(query)
  → 并行：FTS5 BM25 + Embedding 余弦相似度 + R(t) 查表
  → 归一化 + 加权融合
  → 按 TIER 级别取 top-3
  → 注入 system prompt
```

### Cron 路径（每日）
```
03:00 — curve-memory-forgetting.py：
  → 所有记忆 t += 1
  → 计算 R(t)，检测成熟度
  → 若 t ≥ 30 则归档（遗忘或成熟）
  → 更新 ACTIVITY.yaml

03:45 — curve-memory-indexer.py --incremental：
  → 扫描 active/ 的 mtime 变化
  → 对变更文件重新分块 + 嵌入
  → 清理过期索引（已归档主题）
```

## 性能

| 操作 | 延迟 | 说明 |
|------|------|------|
| FTS5 BM25 检索 | < 5ms | SQLite 虚拟表 |
| 嵌入（1 个 chunk） | ~40ms | Ollama qwen3-embedding:8b |
| 三路融合 | < 1ms | 内存字典运算 |
| **总检索** | **~50ms** | 三路全开 |
| 全量索引重建 | ~2 分钟 | 13 个文件 |
| 增量索引 | ~10s | 仅变更文件 |

500 条记忆的预估索引尺寸：< 10 MB（嵌入）+ < 5 MB（FTS5）。

## 安装

### 前置依赖

```bash
# 安装 Ollama 并拉取嵌入模型
ollama pull qwen3-embedding:8b

# Python 依赖（用于余弦相似度）
pip install numpy
```

### 插件安装

```bash
# 从 GitHub 安装
hermes plugins install git@github.com:sin1111yi/curve-memory.git

# 启用并配置
hermes plugins enable curve-memory
hermes config set memory.plugin curve-memory

# 初始化索引
curve-memory index --rebuild

# 重启 gateway
hermes gateway restart
```

### 验证安装

```bash
# 检查插件状态
hermes plugins list | grep curve

# 运行健康检查
curve-memory check

# 测试检索
curve-memory search "R(t) 公式"
```

## CLI 参考

```bash
# 三路混合检索
curve-memory search <查询>                 三路混合检索
curve-memory search <查询> --json           JSON 输出（机器可读）

# 系统状态
curve-memory status                        TIER 分布 + 索引健康
curve-memory stats                         详细统计（平均 R(t)、TIER、索引大小）
curve-memory config                        查看当前配置
curve-memory check                         全面健康检查（6 项）
curve-memory plot                          显示 R(t) 曲线 ASCII 图

# 记忆管理
curve-memory touch <主题>                   重置 t=0，增加访问计数
curve-memory forget <主题>                  手动归档
curve-memory mature <主题>                  标记为成熟
curve-memory recover <主题>                 从 archive 恢复
curve-memory recover --list                 列出可恢复的主题
curve-memory undo                           显示最近操作

# 索引
curve-memory index --rebuild                全量重建索引
curve-memory index --incremental            增量更新（按 mtime）
curve-memory repair                         诊断并修复常见问题
curve-memory repair --fix                   自动修复

# 生命周期
curve-memory setup                          初始化：复制 cron 脚本、注册任务、检查模型
curve-memory install-wizard                 交互式安装向导（7 项检查）
curve-memory activate                       重新启用
curve-memory deactivate                     停用（保留数据）
curve-memory uninstall [--all] [-y]         卸载：清理 cron、配置、可选数据
curve-memory export [文件.tar.gz]           导出所有记忆数据

# 每日
curve-memory daily-tick                     手动触发每日衰减
```

## 相关项目

- [ralqlator](https://github.com/sin1111yi/ralqlator) — Rust 命令行计算器，用于实时验证 R(t) 公式（`ralqlator "0.462 + 0.538 * pow(C_E, -t / 2.71)"`）

## 项目结构

```
~/.hermes/plugins/curve-memory/
├── plugin.yaml                  # 插件元数据
├── __init__.py                  # 注册入口
├── README.md                    # 英文文档
├── README-zh.md                 # 本文件（中文文档）
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

## 存储结构

```
~/.hermes/memories/
├── ACTIVITY.yaml              # t, access_count, mature, protected 标志
├── MEMORY.md                  # idx:topic [t=N] → active/topic.md
├── active/                    # 活跃记忆文件（13 个）
├── .embedding_index/          # 每个 topic 的 JSONL 向量索引 (768-4096d)
├── .fts5/curve_memory_fts5.db # SQLite FTS5 全文索引
├── archive/
│   ├── forgotten/             # 冷存储（可恢复）
│   └── mature/                # 永久知识快照
└── FORGET_LOG.md              # 归档事件日志
```

## 设计决策

| 决策 | 理由 |
|------|------|
| **艾宾浩斯曲线 vs 对数** | 数据驱动，有界 [0.462, 1.0]，无 ∞ 特殊值 |
| **Qwen3-8B (4096维) vs MiniLM (384维)** | 更精细语义粒度，更好的跨语言（中/英）能力 |
| **Ollama vs sentence-transformers** | 零 Python ML 依赖，独立服务，多模型支持 |
| **YAML vs SQLite 存储活跃度** | 人类可读，脚本友好，agent 可直接编辑 |
| **双层归档 vs 单层** | 高频使用的记忆应被提升而非删除 |
| **文件锁 vs 数据库事务** | 简单，足够应对单人 cron 冲突 |
| **Importlib 加载带连字符脚本** | 保持与 ~/.hermes/scripts/ 的向后兼容 |

## 路线图

- [x] Phase 0: 准备与备份
- [x] Phase 1: 遗忘曲线核心（R(t), TIER, cron 衰减）
- [x] Phase 2: 语义检索（FTS5 + Embedding + R(t) 融合）
- [x] Phase 3: Hermes Plugin 封装与 CLI
- [x] Phase 4: 集成与端到端验证
- [ ] Phase 5: 长期调优（α/β/γ 权重、TIER 阈值、成熟度参数）

## 许可

MIT
- [ralqlator](tools/ralqlator) — CLI calculator for R(t) formula verification and math computation
