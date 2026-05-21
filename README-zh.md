# curve-memory — 遗忘曲线记忆系统

> **⚠️ Alpha — 测试阶段。本插件接管 Hermes 的记忆系统和用户画像，当前版本可能包含未发现的问题，包括但不限于数据丢失、双系统冲突、配置失效等。请先在测试环境中验证，确认后再在正式环境使用。**
>
> **⚠️ Alpha — testing phase. This plugin takes over Hermes' memory system and user profile. The current version may contain undiscovered issues including but not limited to data loss, dual-system conflicts, and configuration failures. Validate in a test environment before using in production.**

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
│    ├─ get_tool_schemas() → curve_memory_search 工具   │
│    └─ get_config_schema() / save_config()            │
│                                                      │
└──────────────────────┬───────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────┐
│  核心引擎                                      │
│                                                      │
│  tier.py         R(t) 公式 + TIER 映射                │
│  search.py       BM25 + Embedding + R(t) 融合          │
│  activity.py     ACTIVITY.yaml 读写                   │
│  embedding.py    ABC EmbeddingProvider + 工厂函数       │
│  config.py       get_config_schema, 加载/保存配置       │
│                                                      │
│  agent/provider.py    MemoryProvider 实现              │
│  core/                R(t), 检索, 活跃度, 嵌入, 配置   │
│  backends/            Ollama 嵌入客户端                │
│  enrichment.py        降级, 归档, 丰富, index_sweep    │
└──────────────────────┬───────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────┐
│  Hermes 托管 (~/.hermes/)                              │
│                                                      │
│  memories/           记忆 + 画像 + 嵌入 + FTS5         │
│  cron/jobs.json      索引更新 cron（自动注册）          │
│  scripts/curve-memory-index-sweep.py  独立 cron 入口   │
│  curve-memory-config.json   插件配置文件               │
```

## 数据流

### 写入路径
```python
agent 写入 active/<topic>.md
  → index_sweep()（initialize 时懒加载，或每日 03:00 cron）:
    → 检查 .jsonl mtime vs .md mtime
    → 分块（每块 ≤2000 字符）
    → 通过 Ollama qwen3-embedding:8b 嵌入（/api/embed，60s 超时）
    → 写入 .embedding_index/<topic>.jsonl
  → (FTS5 索引：在线更新，sync_turn() 在对话中同步)
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

### 惰性归档（initialize/on_session_end 时触发）
```
initialize() / on_session_end():
  → 扫描活跃记忆
  → 从 Unix 时间戳计算 R(t)（无 day-counter）
  → 检测成熟度（访问次数 ≥ 20 且 t ≤ 3 天）
  → 若 t ≥ archive_threshold_days 则归档
    → 成熟 → 复制到 archive/mature/ + 提升到 knowledge/
    → 遗忘 → 移动到 archive/forgotten/
  → 更新 ACTIVITY.yaml
```

### 索引更新 cron（每日凌晨 3:00）

```cron
initialize():
  → 如果 embedder 可用: 立刻跑一次 index_sweep()（懒加载，即时补全）
  → 注册每日 cron（no_agent 模式，幂等，已注册则跳过）

每日 03:00（通过 Hermes cron 调度器）:
  → 脚本: ~/.hermes/scripts/curve-memory-index-sweep.py
  → 对每个活跃 topic: 检查 .jsonl mtime vs .md mtime
    → 缺失或过期 → 分块（≤2000 字符）→ 嵌入 → 写入 .jsonl
  → 清理孤立 .jsonl（topic 已不在 ACTIVITY.yaml 中的）
```

| 项目 | 说明 |
|------|------|
| 执行时间 | 每日 03:00（可通过 `jobs.json` 调整） |
| 模式 | `no_agent`（纯脚本，不消耗 LLM token） |
| 耗时 | ~1 分钟/10 个 topic，增量 ~10s |
| 脚本位置 | `~/.hermes/scripts/curve-memory-index-sweep.py` |
| 自动注册 | `initialize()` 中 `_register_index_cron()` — 一次性，幂等 |
| 清理 | `shutdown()` 中清理过期 job |

## 配置

存储在 `{hermes_home}/curve-memory-config.json`（JSON 格式，非 config.yaml）。

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `model` | Ollama 嵌入模型名称 | `qwen3-embedding:8b` |
| `base_url` | Ollama 服务器地址 | `http://localhost:11434` |
| `search_alpha` | BM25 权重 (0-1) | `0.35` |
| `search_beta` | 语义嵌入权重 (0-1) | `0.45` |
| `search_gamma` | 新鲜度权重 (0-1) | `0.20` |
| `archive_days` | 归档前保留天数 (0=永不) | `30` |

通过交互式向导配置：
```bash
hermes curve-memory config --interactive
```

或通过环境变量：
- `CURVE_MEMORY_EMBEDDING_MODEL`
- `CURVE_MEMORY_EMBEDDING_URL`
- `CURVE_MEMORY_ALPHA`
- `CURVE_MEMORY_BETA`
- `CURVE_MEMORY_GAMMA`
- `CURVE_MEMORY_ARCHIVE_DAYS`

## 接管范围

curve-memory 完全接管两个系统：

| 组件 | 内置（默认） | curve-memory |
|------|-------------|--------------|
| **记忆系统** | `MEMORY.md` — 平面键值存储 + `idx:` 索引 | `active/*.md` — 每个主题一个文件，遗忘曲线 R(t)，混合检索 |
| **用户画像** | `USER.md` — 由 Hermes `memory` 工具管理 | `USER.md`（`memories/` 目录下）— 自然语言，通过 `system_prompt_block()` 注入 |

插件提供 4 个工具：`curve_memory_search`（记忆召回）、`curve_memory_user_get/set/delete`（画像管理）。

## 从内置记忆迁移

如果已有 Hermes 内置记忆数据，按以下步骤迁移：

### 1. 记忆数据（MEMORY.md）

`idx:` 索引格式已经兼容——无需迁移。已有的 `active/*.md` 文件立即可被识别。

> **注意：** `MEMORY.md` 本身**不会**被迁移到 `active/*.md`——它只是索引。实际记忆内容已通过记忆索引协议由 agent 写入 `active/*.md`。如果你的 `MEMORY.md` 中有非索引的键值条目，agent 在禁用内置系统之前仍然可以读取它们。

### 2. 用户画像（USER.md）

```bash
# 从内置记忆导出用户画像
hermes memory get > ~/user-profile-backup.txt

# 后续可通过 curve_memory_user_set 导入，
# 或直接编辑 ~/.hermes/memories/USER.md
```

### 3. 禁用内置记忆（可选，迁移完成并验证后）

```bash
hermes config set memory.memory_enabled false
hermes config set memory.user_profile_enabled false
hermes gateway restart
```

这将会移除内置的 `memory` 工具和 `USER.md`。curve-memory 的工具和提示注入成为唯一来源。

## 性能

| 操作 | 延迟 | 说明 |
|------|------|------|
| FTS5 BM25 检索 | < 5ms | SQLite 虚拟表 |
| 嵌入（1 个 chunk） | ~40ms | Ollama qwen3-embedding:8b |
| 三路融合 | < 1ms | 内存字典运算 |
| **总检索** | **~50ms** | 三路全开 |
| 全量索引重建 | ~1 分钟/10 个文件 | 取决于 topic 数量 |
| 增量索引 | ~10s | 仅变更文件 |

500 条记忆的预估索引尺寸：< 10 MB（嵌入）+ < 5 MB（FTS5）。

## 安装

### 前置依赖

```bash
# 安装 Ollama 并拉取嵌入模型
ollama pull qwen3-embedding:8b
```

### 插件安装

```bash
# 1. 从 GitHub 安装
hermes plugins install https://github.com/sin1111yi/curve-memory.git

# 2. 启用插件
hermes plugins enable curve-memory

# 3. 交互式配置（模型、搜索权重、归档阈值）
hermes curve-memory config --interactive

# 4. 启用记忆提供者
hermes config set memory.provider curve-memory

# 5. 重建索引
hermes curve-memory index --rebuild

# 6. 重启 gateway
hermes gateway restart

# 7. 验证
hermes curve-memory check
hermes curve-memory status
```

## CLI 参考

所有命令通过 Hermes 子命令调用：

```bash
hermes curve-memory <命令> [参数]
```

### 7 个命令

| 命令 | 说明 | 参数 |
|------|------|------|
| `search <关键词>` | 三路混合检索 | `--json`, `--top-k N` |
| `status` | 系统状态 + TIER 分布 | — |
| `config` | 查看/配置 | `--interactive` (配置向导) |
| `check` | 健康检查（5 项） | — |
| `activate` | 重新激活 curve-memory | — |
| `deactivate` | 停用（保留数据） | — |
| `index` | 构建索引 | `--rebuild` (全量重建) |

### 检索

```bash
hermes curve-memory search "借用检查器"           # 三路检索
hermes curve-memory search "R(t) 公式" --json     # JSON 输出
```

### 系统状态

```bash
hermes curve-memory status         # TIER 分布 + 索引健康
hermes curve-memory config         # 查看配置
hermes curve-memory check          # 健康检查（5 项）
```

### 配置

```bash
hermes curve-memory config                          # 查看当前配置
hermes curve-memory config --interactive            # 交互式配置向导
```

### 激活/停用

```bash
hermes curve-memory activate         # 启用（设置 memory.provider）
hermes curve-memory deactivate       # 停用（保留数据）
```

### 索引

```bash
hermes curve-memory index            # 增量更新
hermes curve-memory index --rebuild  # 全量重建
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
├── after-install.md             # 安装后指引
├── scripts/
│   └── curve-memory-index-sweep.py   # cron 独立入口
└── curve_memory/
    ├── __init__.py               # 包标记
    ├── provider.py               # MemoryProvider 实现
    ├── cli.py                    # CLI 工具（7 个命令）
    ├── enrichment.py             # 降级、归档、丰富、index_sweep
    ├── core/
    │   ├── __init__.py
    │   ├── tier.py               # R(t) 引擎 + TIER 映射
    │   ├── search.py             # 三路混合检索
    │   ├── activity.py           # ACTIVITY.yaml 读写
    │   ├── embedding.py          # ABC EmbeddingProvider + 工厂
    │   └── config.py             # 配置 schema、加载/保存
    ├── backends/
    │   ├── __init__.py
    │   └── ollama.py             # Ollama 嵌入客户端
    └── skill/
        └── SKILL.md              # Agent 协议文档
```

## 存储结构

```
~/.hermes/memories/
├── ACTIVITY.yaml              # t（时间戳）, access_count, mature, protected 标志
├── MEMORY.md                  # idx:topic [t=N] → active/topic.md
├── active/                    # 活跃记忆文件（*.md）
├── .embedding_index/          # 每个 topic 的 JSONL 向量索引（*.jsonl）
├── .fts5/curve_memory_fts5.db # SQLite FTS5 全文索引
├── archive/
│   ├── forgotten/             # 冷存储（可恢复）
│   └── mature/                # 永久知识快照
├── USER.md                    # 用户画像（自然语言 + ## Auto）
└── .tier_cache.json           # TIER 缓存（用于 detect_tier_drops）

~/.hermes/
├── scripts/curve-memory-index-sweep.py   # cron 脚本（自动复制）
├── cron/jobs.json                        # cron 注册（自动管理）
└── curve-memory-config.json              # 插件配置文件
```

## 设计决策

| 决策 | 理由 |
|------|------|
| **艾宾浩斯曲线 vs 对数** | 数据驱动，有界 [0.462, 1.0]，无 ∞ 特殊值 |
| **Qwen3-8B (4096维) vs MiniLM (384维)** | 更精细语义粒度，更好的跨语言（中/英）能力 |
| **Ollama vs sentence-transformers** | 零 Python ML 依赖，独立服务，多模型支持 |
| **YAML vs SQLite 存储活跃度** | 人类可读，脚本友好，agent 可直接编辑 |
| **双层归档 vs 单层** | 高频使用的记忆应被提升而非删除 |
| **基于时间戳的 R(t) vs cron** | 无需 cron，查询时从 Unix 时间戳实时计算 |
| **惰性归档 vs 每日 cron** | 归档在 initialize/end-session 时触发，无需定时脚本 |
| **cron 驱动索引更新 vs 在线索引** | 嵌入计算每日 03:00 通过 no_agent 脚本运行；新记忆 24h 内自动索引，不阻塞对话 |
| **JSON 配置 vs YAML 配置段** | 独立文件，兼容 get_config_schema() / save_config() |
| **`memory.provider` vs `memory.plugin`** | 标准 ABC MemoryProvider 接口 |

## MemoryProvider 实现

本插件实现了完整的 `MemoryProvider` 抽象基类：

| 方法 | 用途 |
|------|------|
| `initialize()` | 创建资源，加载配置，初始化嵌入引擎，执行惰性归档 |
| `prefetch(query)` | 每轮对话前调用——注入最多 3 条相关记忆 |
| `sync_turn(user, asst)` | 每轮对话后调用——更新提到主题的活性 |
| `system_prompt_block()` | 记忆系统的简短描述 |
| `get_tool_schemas()` | 返回 4 个工具的 OpenAI function-calling 格式定义（见下文） |
| `handle_tool_call()` | 处理所有 4 个工具调用 |
| `get_config_schema()` | 为 `hermes memory setup` 提供配置 schema |
| `save_config(values, hermes_home)` | 从 schema values 保存配置 |
| `on_session_end(messages)` | 对话结束时惰性归档 |
| `shutdown()` | 清理资源 |

## 工具

插件暴露 4 个工具供 agent 调用：

| 工具 | 用途 | 主要参数 |
|------|------|----------|
| `curve_memory_search` | 三路混合记忆检索 | `query` (str), `top_k` (int, 默认 5) |
| `curve_memory_user_get` | 获取全部用户画像条目 | — |
| `curve_memory_user_set` | 存储用户信息（跨会话持久化） | `key` (str), `value` (str) |
| `curve_memory_user_delete` | 删除用户信息 | `key` (str) |

### 用户画像

用户画存储于 `{hermes_home}/memories/USER.md`，使用自然语言格式。
通过 `system_prompt_block()` 注入系统提示，`prefetch()` 可根据查询关键词匹配。

文件含两个区域：
- **手动区**（顶部）：自由格式自然语言——直接编辑
- **## Auto 区**（底部）：工具生成的条目——通过 `curve_memory_user_set`/`delete` 管理

## 路线图

- [x] Phase 0: 准备与备份
- [x] Phase 1: 遗忘曲线核心（R(t), TIER, 衰减）
- [x] Phase 2: 语义检索（FTS5 + Embedding + R(t) 融合）
- [x] Phase 3: Hermes Plugin 封装与 CLI
- [x] Phase 4: 集成与端到端验证
- [x] Phase 5: MemoryProvider ABC 重构
- [ ] Phase 6: 长期调优（α/β/γ 权重、TIER 阈值、成熟度参数）

## 许可

MIT
