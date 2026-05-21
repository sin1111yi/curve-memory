# ADR-005: 最终形态记忆系统（遗忘曲线 × 语义检索 × Plugin 封装）

**状态：** 📐 设计完成（待实现）
**时间：** 2026-06-01
**融合自：** ADR-003 (遗忘曲线) + ADR-004 (语义检索 & Plugin)
**替代：** ADR-003 & ADR-004 各自独立设计 → 统一落地

---

## 目录

1. [设计哲学](#1-设计哲学)
2. [核心公式体系](#2-核心公式体系)
3. [TIER 分级体系](#3-tier-分级体系)
4. [双层归档机制](#4-双层归档机制)
5. [三路混合检索](#5-三路混合检索)
6. [数据流全链路](#6-数据流全链路)
7. [Plugin 封装方案](#7-plugin-封装方案)
8. [Edge Cases & 容错](#8-edge-cases--容错)
9. [回滚方案](#9-回滚方案)
10. [分阶段实施计划](#10-分阶段实施计划)
11. [附录：关键代码骨架](#11-附录关键代码骨架)

---

## 1. 设计哲学

### 1.1 两个设计的定位与融合逻辑

```
ADR-003（遗忘曲线）        ADR-004（语义检索 & Plugin）
  ┌─────────────────┐       ┌─────────────────────────┐
  │ R(t) 计算引擎     │       │ 三路混合检索              │
  │ TIER 分级         │  +   │   BM25 + embedding + R(t)│
  │ 双层归档           │       │ Plugin 封装              │
  │ cron 衰减          │       │ CLI 接口                │
  └─────────────────┘       └─────────────────────────┘
         ↓                           ↓
  ┌─────────────────────────────────────────────────────┐
  │  ADR-005: 最终形态                                     │
  │  ├─ R(t) 引擎：ADR-003 的公式 + TIER + 归档不变         │
  │  ├─ 检索增强：ADR-004 的三路融合叠加到 TIER 之上          │
  │  ├─ Plugin 封装：ADR-004 的 CLI + Provider + Skill      │
  │  └─ 实施：第一阶段纯遗忘曲线，第二阶段叠加语义检索          │
  └─────────────────────────────────────────────────────┘
```

### 1.2 核心原则

1. **渐进增强，允许降级** — 纯遗忘曲线系统（ADR-003）是基础，语义检索（ADR-004）是增强层。无 embedding 时系统照常运作。
2. **数据格式与框架解耦** — YAML/Markdown/JSONL/SQLite，不依赖 Hermes 内部对象格式。
3. **每个阶段可独立交付** — 第 1 阶段落地后系统即可正常工作，第 2 阶段叠加语义检索时零迁移成本。
4. **永不丢失核心知识** — 基线保留率 46.2% 保证核心概要永远可恢复。

---

## 2. 核心公式体系

### 2.1 遗忘曲线公式（继承 ADR-003）

```math
R(t) = 0.462 + 0.538 · exp(-t / 2.71)

其中：
  t = 距离上次访问的天数（t ≥ 0）
  R = 记忆保留率（无量纲，值域 [0.462, 1.0]）
```

**ralqlator 兼容实现：**
```
R = 0.462 + 0.538 * pow(C_E, -t / 2.71)
```

**关键特性：**

| t | R(t) | 物理意义 |
|---|------|---------|
| 0 | 1.000 | 刚刚使用，全部保留 |
| 1 | 0.871 | 1天后，仍保留87% |
| 2.71 (τ) | 0.660 | 一个时间常数后保留66% |
| 7 | 0.503 | 一周后，保留50% |
| 14 | 0.480 | 两周后，保留48% |
| 30 | 0.4628 | 一个月后，接近基线 |
| ∞ | 0.462 | 永不归零的基线保留率 |

### 2.2 三路混合检索公式（继承 ADR-004）

```math
最终排序分 = α · BM25_score + β · cosine_sim + γ · R(t)

默认权重：
  α = 0.35 (BM25 关键词精确匹配)
  β = 0.45 (embedding 语义相似度)
  γ = 0.20 (R(t) 新鲜度)
```

**权重设计理由：**
- α (0.35)：用户常用精确术语（"ACTIVITY.yaml"、"R(t)"、"E0495"），关键词精确匹配不可替代
- β (0.45)：最高权重——语义匹配覆盖关键词盲区，是整个检索增强的核心增益
- γ (0.20)：保底权重——太久远的记忆即使语义匹配再高也不应冲顶；但不设过高以免新记忆总是压倒一切

### 2.3 两条公式的关系

```
三层独立 → 归一化 → 加权融合 → 最终排序

R(t) 同时在两个地方起作用：
  1. 遗忘曲线系统：决定 TIER 分级、归档时机、内容详细度
  2. 三路检索：作为 γ·R(t) 项提供新鲜度排序修正

两条公式不冲突——R(t) 在检索分中是 0.20 权重因子，
在 TIER 分级中是唯一决定因素。
```

---

## 3. TIER 分级体系

### 3.1 六级 TIER 映射表（继承 ADR-003）

| TIER | R(t) 下界 | t (天数) | 详细度 | 行为 |
|------|-----------|----------|--------|------|
| TIER_5 🔥 | R ≥ 0.800 | t ≤ 1 | 完整详细，全部章节 | 全量加载，参与检索全量索引 |
| TIER_4 📗 | R ≥ 0.640 | t ≤ 3 | 详细，核心事实 + 关键细节 | 加载核心+细节，参与检索全量索引 |
| TIER_3 📙 | R ≥ 0.503 | t ≤ 7 | 摘要，要点列表 | 加载摘要，参与检索摘要索引 |
| TIER_2 📕 | R ≥ 0.465 | t ≤ 14 | 极简，一行概要 | 只读首行/概要，不参与语义索引 |
| TIER_1 📦 | R > 0.462 | 14 < t < 30 | 归档待命 | 索引保留，等待归档 |
| ARCHIVE 🗄️ | R ≈ 0.462 | t ≥ 30 | 归档 | 移出 active/ |

### 3.2 TIER × 检索关联规则（ADR-005 新增融合设计）

这是 ADR-003 和 ADR-004 融合的关键创新点——TIER 级别决定内容的**索引粒度**：

| TIER | 是否参与 FTS5 索引 | 是否参与 Embedding 索引 | 原因 |
|------|-------------------|------------------------|------|
| TIER_5 🔥 | ✅ 全量 | ✅ 全量 chunk | 最新记忆，需要最大可检索性 |
| TIER_4 📗 | ✅ 全量 | ✅ 全量 chunk | 仍有较高检索价值 |
| TIER_3 📙 | ✅ 摘要 | ✅ 摘要句（1 chunk） | 降低索引噪音，保留可检索性 |
| TIER_2 📕 | ✅ 摘要 | ❌ 不索引 | 只保留关键词索引 |
| TIER_1 📦 | ✅ 仅 topic name | ❌ 不索引 | 只保留路径可寻 |
| ARCHIVE 🗄️ | ❌ 清除索引 | ❌ 清除索引 | 已归档 |

**此规则的意义：**
- 避免低价值记忆（TIER_2 以下）的 embedding 索引污染检索结果
- 减少索引总量：约 40% 的记忆只参与关键词索引
- 当 TIER 降级时，自动触发索引裁剪（在每日 cron 中执行）

---

## 4. 双层归档机制

### 4.1 遗忘归档（Forgetting Archive）

**触发条件：** `t ≥ 30`（R ≈ 0.462）且 `mature == false`

```
cron 触发：
  1. 计算 R = 0.462 + 0.538 * exp(-t/2.71)
  2. 检查 memory-system 保护标记 — 跳过受保护记忆
  3. 检查 mature 标记：
     a. mature == true  → 执行成熟归档（4.2）
     b. mature == false → 执行遗忘归档
  4. 遗忘归档动作：
     a. mv active/<topic>.md → archive/forgotten/<topic>.md
     b. 从 MEMORY.md 中删除对应的 idx 条目
     c. 从 ACTIVITY.yaml 中删除该记忆条目
     d. 清理 .embedding_index/<topic>.jsonl（如果存在）
     e. 清理 FTS5 中该 topic 的条目
     f. 写入 FORGET_LOG.md
```

### 4.2 成熟归档（Mature Archive）

**成熟度判定算法：**
```
is_mature(topic):
  满足任一即可：
  1. access_count ≥ 20 且 t ≤ 3 → 高频使用 → 成熟
  2. 用户或 agent 主动设置 mature: true
```

**触发时机：** 记忆同时满足 `mature == true` 且 `t ≥ 30`

```
执行：
  1. 复制 active/<topic>.md → archive/mature/<topic>.md
  2. 提炼为永久知识文档 knowledge/<topic>.md
  3. 删除 active/<topic>.md
  4. 从 MEMORY.md 删除 idx 条目
  5. 从 ACTIVITY.yaml 删除该条目
  6. 清理 .embedding_index/<topic>.jsonl 及 FTS5 条目
  7. 在 FORGET_LOG.md 中记录为「成熟归档」
```

### 4.3 目录结构（完整版）

```
~/.hermes/memories/
├── ACTIVITY.yaml              ← 遗忘曲线系统（t, access_count, mature, protected）
├── MEMORY.md                  ← 索引 + 小型事实 (memory tool 管理)
├── USER.md                    ← 用户资料 (不变)
├── active/                    ← 活跃记忆 (t < 30)
│   ├── workflow.md
│   ├── rust-lifetimes.md
│   └── ...
├── .embedding_index/          ← [ADR-004 增量] chunk → vector 映射
│   ├── workflow.jsonl
│   ├── rust-lifetimes.jsonl
│   └── ...
├── .embedding_meta.yaml       ← [ADR-004 增量] 模型、版本、维度信息
├── .fts5/                     ← [ADR-004 增量] SQLite FTS5 索引
│   └── curve_memory_fts5.db
├── archive/
│   ├── forgotten/             ← 遗忘归档（可重新激活）
│   │   └── FORGET_LOG.md
│   └── mature/                ← 成熟归档（永久知识快照）
└── knowledge/                 ← 成熟记忆升级的永久知识
    ├── workflow.md
    └── ...
```

---

## 5. 三路混合检索

### 5.1 架构总图

```
用户消息 → query
  │
  ├──→ FTS5 (SQLite .fts5/curve_memory_fts5.db)
  │      ↓
  │      BM25_score  ← 对 active/*.md 的全文索引
  │
  ├──→ Embedding Provider (sentence-transformers / ollama)
  │      ↓
  │      cosine_sim(.embedding_index/*.jsonl)  ← 按 chunk 级内容
  │
  └──→ 查 ACTIVITY.yaml
         ↓
         R(t) = 0.462 + 0.538 · exp(-t/2.71)
  │
  └──→ 三路归一化 + 加权融合 (α=0.35, β=0.45, γ=0.20)
         ↓
         top-K topics
         ↓
         查 ACTIVITY.yaml → R(t) → TIER 映射 → 决定读取深度
         ↓
         read_file(active/<topic>.md, depth=TIER)
         ↓
         注入 system prompt
         ↓
         agent 回复后 touch topic（t=0, access_count++）
```

### 5.2 三路并行执行设计

三路检索**并行执行，融合后才排序**——不串行，不互相等待：

| 检索路 | 数据源 | 耗时 | 实现方式 |
|--------|--------|------|---------|
| ① FTS5 BM25 | `.fts5/curve_memory_fts5.db` | < 5ms | SQLite FTS5 MATCH |
| ② Embedding cosine_sim | `.embedding_index/*.jsonl` | ~40ms (含 embed) | sentence-transformers + numpy |
| ③ R(t) 查表 | ACTIVITY.yaml (内存缓存) | < 1ms | 字典查找 |

总增量延迟约 **46ms**（详见附录延时分解）。

### 5.3 归一化策略

三路得分的值域不同，需要归一化到 [0, 1]：

```python
def normalize_bm25(raw_scores: dict[str, float]) -> dict[str, float]:
    """BM25 得分 min-max 归一化"""
    if not raw_scores:
        return {}
    max_s = max(raw_scores.values())
    min_s = min(raw_scores.values())
    span = max_s - min_s if max_s > min_s else 1.0
    return {k: (v - min_s) / span for k, v in raw_scores.items()}

def normalize_cosine(raw_scores: dict[str, float]) -> dict[str, float]:
    """cosine 相似度已在 [0,1]，通常无需额外归一化"""
    return raw_scores

def normalize_r(raw_values: dict[str, float]) -> dict[str, float]:
    """R(t) 天然在 [0.462, 1.0]，归一化到 [0, 1]"""
    return {k: (v - 0.462) / 0.538 for k, v in raw_values.items()}
```

### 5.4 Chunk 策略（继承 ADR-004）

按 Markdown `##`（H2）章节标题分割：

```jsonl
{"topic": "rust-lifetimes", "chunk": "核心事实", "text": "生命周期标注用 'a 语法...", "mtime": "2026-06-01", "vector": [0.12, -0.34, ...]}
{"topic": "rust-lifetimes", "chunk": "常见错误", "text": "E0495: 返回值需要显式生命周期...", "mtime": "2026-06-01", "vector": [...]}
```

### 5.5 Embedding Provider 设计

**默认模型：** `sentence-transformers/all-MiniLM-L6-v2`（384 维，CPU ~30ms）

**可配置的 Provider 机制：**

```yaml
# ~/.hermes/config.yaml
memory:
  plugin: curve-memory
  embedding:
    provider: sentence-transformers   # 可选: ollama
    model: all-MiniLM-L6-v2
    base_url: http://localhost:11434   # 仅 ollama 需要
```

**Provider 抽象接口：**

```python
class EmbeddingProvider(ABC):
    @abstractmethod
    def embed(self, text: str) -> list[float]: ...
    @abstractmethod
    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...

class SentenceTransformersProvider(EmbeddingProvider):
    def __init__(self, model_name="all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(model_name)

class OllamaProvider(EmbeddingProvider):
    def __init__(self, model="nomic-embed-text", base_url="http://localhost:11434"):
        ...
```

**冷启动行为：** Provider 在 `CurveMemoryProvider.initialize()` 时实例化。如果配置的 provider 不可用（Ollama 未运行、模型未安装），**不阻断启动**，自动降级为 BM25 + R(t)，并打印告警。

### 5.6 五级降级链（继承 ADR-004）

```
Level 0: 三路全开（完全体）
   BM25 + Embedding + R(t) → 最佳检索效果

Level 1: sentence-transformers 未安装
   两路：BM25 + R(t)（带新鲜度排序的关键词检索）

Level 2: FTS5 索引损坏
   两路：Embedding + R(t)（纯语义 + 新鲜度）

Level 3: Embedding + FTS5 均不可用
   单路：R(t) 排序（仅按新鲜度，纯关键词匹配 topic name）

Level 4: 全不可用
   纯关键词匹配（ADR-003 原始设计，无 embedding，无 FTS5）
```

每一级降级不影响 agent 的调用接口——`curve-memory-cli search` 始终返回相同格式的结果。

---

## 6. 数据流全链路

### 6.1 写入路径

```
agent 写 active/<topic>.md
  → curve-memory-indexer (cron 或 inotify) 检测到文件变更
  → 按 H2 标题分 chunk
  → EmbeddingProvider.embed_batch(chunks) → 384d 向量
  → 写入 .embedding_index/<topic>.jsonl
  → 更新 SQLite FTS5（覆盖 active/*.md）
  → 更新 ACTIVITY.yaml（若为新 topic，初始化 t=0, access_count=0）
```

### 6.2 读取路径

```
用户消息 → agent
  → CurveMemoryProvider.prefetch(query)
  → 并行三路检索：
     FTS5 BM25({active/ files}) → 归一化 BM25_score
     EmbeddingProvider.embed(query) → cosine_sim(.embedding_index/*) → 归一化 cosine_score
     ACTIVITY.yaml lookup → R(t) → 归一化 R(t)_score
  → final_score = 0.35·BM25 + 0.45·cosine + 0.20·R(t)
  → top-5 topics
  → 按 TIER 决定读取深度
  → read_file → 注入 system prompt
```

### 6.3 Cron 写入路径

```
curve-memory-forgetting.py（每日 03:00）:
  → 读取 ACTIVITY.yaml
  → 所有记忆 t += 1
  → 计算 R(t)
  → 检查哪些需要归档
  → 执行遗忘/成熟归档
  → 清理对应的 embedding 和 FTS5 索引
  → 写回 ACTIVITY.yaml

curve-memory-indexer.py（每日 04:00，或文件变更触发）:
  → 扫描 active/*.md 的 mtime
  → 变动的文件 re-chunk → re-embed → 替换 .jsonl + 更新 FTS5
  → 归档的文件 → 清理 .embedding_index/ + 清理 FTS5
```

### 6.4 温度回弹（R(t) → 1.0）时的内容恢复

```
当记忆被重新使用（t=0, R=1.0）:
  1. t=0 → R=1.0 → TIER_5
  2. agent 检查当前 active/<topic>.md 内容
  3. 如果内容比 TIER_5 更少（例如处于 TIER_3 时期被重新激活）
  4. agent 根据当前对话上下文补全内容至 TIER_5 规范
  5. 写回 active/<topic>.md
  6. 更新 ACTIVITY.yaml 中 t=0, access_count += 1
  7. curve-memory-indexer 检测到变更 → 重新索引 embedding + FTS5
```

### 6.5 跨会话记忆传承

```
对话 A 结束 → sync_turn() 更新所有涉及记忆的 t 值
  → 对话 A 中新建/修改的记忆写入 active/
  → curve-memory-indexer 构建索引
  → 下次对话 B 启动
  → prefetch() 检索到该记忆（即使对话 A 已结束）
  → 注入 system prompt
  → agent 自动使用
```

---

## 7. Plugin 封装方案

### 7.1 架构总览

```
┌─────────────────────────────────────────────────────────┐
│  Plugin: curve-memory                                   │
│                                                         │
│  对外提供：                                               │
│  ┌────────────┐  ┌──────────┐  ┌──────────┐  ┌───────┐ │
│  │ CLI 命令    │  │ Provider  │  │ Cron     │  │ Tool  │ │
│  │ curve-     │  │ (Curve-  │  │ (t++,    │  │ (语义 │ │
│  │ memory-cli │  │ Memory-  │  │ index)   │  │ 搜索) │ │
│  │            │  │ Provider)│  │          │  │       │ │
│  └────────────┘  └──────────┘  └──────────┘  └───────┘ │
│                                                         │
│  持久化层：                                               │
│  ~/.hermes/memories/ (YAML + Markdown + JSONL + SQLite)  │
└─────────────────────────────────────────────────────────┘
```

### 7.2 组件清单

| 组件 | 文件 | 说明 |
|------|------|------|
| **MemoryProvider** | `provider.py` | `CurveMemoryProvider` 实现 `prefetch()`（三路检索 + TIER 注入）和 `sync_turn()`（更新 t 值） |
| **CLI** | `cli.py` | `curve-memory-cli` 子命令入口 |
| **检索核心** | `search.py` | 三路检索核心（FTS5 + cosine + R(t) 融合） |
| **索引管理** | `indexer.py` | Embedding + FTS5 索引构建和增量更新 |
| **YAML 读写** | `activity.py` | ACTIVITY.yaml 的读写、迁移、缓存 |
| **TIER 计算** | `tier.py` | R(t) 计算 + TIER 映射 + 归档判定 |
| **Chunk 分割** | `chunker.py` | Markdown H2 章节分割 |
| **Cron 脚本** | `scripts/curve-memory-forgetting.py` | 每日衰减 + 归档 |
| **Cron 脚本** | `scripts/curve-memory-indexer.py` | 每日索引更新 |
| **Skill 文档** | `skill/SKILL.md` | agent 协议文档（R(t) 公式、TIER 映射、行为规则） |

### 7.3 CLI 命令

```bash
hermes curve-memory-cli init                     # 初始化目录结构 + ACTIVITY.yaml
hermes curve-memory-cli search "query"           # 三路检索 → top-5 + R(t) + 片段
hermes curve-memory-cli search "query" --json    # JSON 格式输出（机器可读）
hermes curve-memory-cli read <topic>             # 按 TIER 读文件内容
hermes curve-memory-cli touch <topic>            # 置 t=0, access_count++
hermes curve-memory-cli status                  # 活跃记忆概览 + TIER 分布 + 索引大小
hermes curve-memory-cli daily-tick              # 手动触发每日衰减
hermes curve-memory-cli index                   # 手动触发 embedding + FTS5 索引更新
hermes curve-memory-cli forget <topic>          # 手动归档
hermes curve-memory-cli mature <topic>          # 手动标记成熟
hermes curve-memory-cli check                   # 健康检查（索引状态、降级级别）
```

### 7.4 Provider 行为

```python
class CurveMemoryProvider(MemoryProvider):
    name = "curve-memory"

    def is_available(self) -> bool:
        """检查 ACTIVITY.yaml 是否存在"""
        return (get_hermes_home() / "memories" / "ACTIVITY.yaml").exists()

    def initialize(self, session_id: str, **kwargs):
        """初始化——加载 ACTIVITY.yaml 缓存，准备 embedding provider，连接 FTS5"""
        config = kwargs.get("config", {})
        emb_cfg = config.get("embedding", {})
        provider_name = emb_cfg.get("provider", "sentence-transformers")
        model_name = emb_cfg.get("model", "all-MiniLM-L6-v2")

        # 尝试加载 embedding provider，失败则降级
        try:
            if provider_name == "ollama":
                self.embedder = OllamaProvider(...)
            else:
                self.embedder = SentenceTransformersProvider(model_name)
        except (ImportError, Exception):
            self.embedder = None  # 降级为 BM25 + R(t)

        # 加载 ACTIVITY.yaml 到内存缓存
        self.activity_cache = self._load_activity()

        # 连接 FTS5 索引
        self.fts5_conn = self._connect_fts5()

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """三路混合检索 → 按 TIER 决定注入内容"""
        if not query.strip():
            return ""
        # 并行三路
        bm25_scores = self.fts5_search(query)
        cosine_scores = self.semantic_search(query) if self.embedder else {}
        r_values = self.get_r_values_for_topics(all_topics)
        # 归一化 + 融合
        final = self.hybrid_fuse(bm25_scores, cosine_scores, r_values)
        # TIER 缩减
        blocks = []
        for topic, text, r in final[:3]:
            tier = r_to_tier(r)
            snippet = self.truncate_by_tier(text, tier)
            blocks.append(f"### {topic} ({tier})\n{snippet}")
        return "## 召回记忆\n" + "\n\n".join(blocks)

    def sync_turn(self, user: str, asst: str):
        """对话结束后更新 touch 过的记忆"""
        ...

    def get_tool_schemas(self) -> list:
        """注册 curve_memory_semantic_search 工具"""
        return [{
            "name": "curve_memory_semantic_search",
            "description": "三路混合检索记忆系统（BM25 + 语义 + 遗忘曲线）",
            "parameters": {...}
        }]
```

### 7.5 Plugin 目录结构

```
~/.hermes/hermes-agent/plugins/memory/curve-memory/
├── __init__.py                ← plugin 入口，注册所有组件
├── provider.py                ← CurveMemoryProvider
├── cli.py                     ← curve-memory-cli CLI 入口
├── search.py                  ← 三路检索核心（FTS5 + cosine + R(t) 融合）
├── indexer.py                 ← embedding + FTS5 索引管理
├── activity.py                ← ACTIVITY.yaml 读写
├── tier.py                    ← R(t) 计算 + TIER 映射
├── chunker.py                 ← Markdown chunk 分割
├── scripts/
│   ├── curve-memory-forgetting.py  ← cron：每日衰减 + 归档
│   └── curve-memory-indexer.py     ← cron：embedding + FTS5 索引更新
└── skill/
    └── SKILL.md               ← agent 协议文档
```

### 7.6 API 定义：活动记录格式（ACTIVITY.yaml v4）

在 ADR-003 v3 格式基础上，增加 embedding 和 FTS5 索引状态字段：

```yaml
# Memory System v4 — 最终形态
# 遗忘曲线: R(t) = 0.462 + 0.538 * exp(-t/2.71)
# 检索: BM25 + Embedding + R(t) 三路混合

metadata:
  format_version: 4
  model: forgetting_curve
  formula: "R(t) = 0.462 + 0.538 * exp(-t/2.71)"
  baseline: 0.462
  archive_threshold_t: 30
  retrieval:
    alpha: 0.35   # BM25 权重
    beta: 0.45    # Embedding 权重
    gamma: 0.20   # R(t) 权重
    embedding_provider: sentence-transformers
    embedding_model: all-MiniLM-L6-v2
    embedding_dim: 384
  created: "2026-06-01"
  last_index: "2026-06-01T04:00:00"

workflow:
  t: 0
  access_count: 42
  mature: true
  indexed: true            # 新增：是否已构建 embedding 索引
  fts5_indexed: true       # 新增：是否已构建 FTS5 索引

rust-learning:
  t: 3
  access_count: 8
  mature: false
  indexed: true
  fts5_indexed: true

memory-system:
  t: 0
  access_count: 999
  mature: true
  protected: true
  indexed: true
  fts5_indexed: true
```

### 7.7 跨框架复用矩阵

| 目标框架 | 接入方式 | 需要什么 |
|----------|---------|---------|
| Hermes (同实例) | `hermes plugins install curve-memory` | Plugin |
| Hermes (其他实例) | 同上 + 拷贝 `~/.hermes/memories/` | Plugin + 数据 |
| Claude Code | Skill 文档 + `!curve-memory-cli search "query"` | CLI + Skill |
| Codex CLI | `run("curve-memory-cli search 'query'")` | CLI + Skill |
| Cursor | `.mdc` 规则 + 终端命令 | CLI + Skill |
| 任何 shell 脚本 | `curve-memory-cli search "query" --format json` | CLI only |
| Python 脚本 | `subprocess.run(["curve-memory-cli", "search", query])` | CLI only |

---

## 8. Edge Cases & 容错

### 8.1 Indexer 与 Forgetting 的竞态条件

**场景：** cron 中 `memory-forgetting.py` 在归档 topic X 的同时，`memory-indexer.py` 正在为 topic X 构建索引。

**解决方案：** 加互斥锁文件 `~/.hermes/memories/.memory.lock`。

```python
# 两个 cron 脚本都使用文件锁
import fcntl

lock_file = MEMORIES_DIR / ".memory.lock"
with open(lock_file, "w") as lf:
    fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
    # 执行操作
    # ...
```

如果锁获取失败（另一个脚本正在运行），脚本退出并记录日志，等待下次 cron 周期。

### 8.2 索引损坏恢复

**场景：** `.embedding_index/*.jsonl` 文件损坏或 `.fts5/curve_memory_fts5.db` 损坏。

**恢复命令：**
```bash
hermes curve-memory-cli index --rebuild   # 完全重建所有索引
hermes curve-memory-cli check             # 健康检查，报告索引状态
```

`check` 命令检查：
- ACTIVITY.yaml 格式是否正确
- 所有 active/ 中的文件是否有对应 embedding 索引
- embedding 向量维度是否与配置一致
- FTS5 表是否可查询

### 8.3 Embedding Provider 切换后的向量维度不一致

**场景：** 用户从 `sentence-transformers`（384d）切换到 `ollama nomic-embed-text`（768d），现有 `.embedding_index/*.jsonl` 中的向量维度不匹配。

**解决方案：** 切换 provider 后在 `.embedding_meta.yaml` 中记录新模型信息，`curve-memory-cli index --rebuild` 时检测到维度变化自动重建全部索引。

```yaml
# .embedding_meta.yaml
version: 2
provider: ollama
model: nomic-embed-text
dimension: 768
last_build: "2026-06-01T04:00:00"
```

### 8.4 受保护记忆的索引逻辑

**场景：** `memory-system` 等受保护记忆不应该被遗忘或归档，它们应该如何参与检索？

**规则：**
- 受保护记忆（`protected: true`）**不参与**遗忘曲线衰减（t 不增加）
- 受保护记忆**参与**三路检索（正常建索引、可被检索到）
- 受保护记忆的 TIER 始终为 TIER_5
- 这意味着 agent 可以始终检索到 `memory-system` 等核心协议文档

### 8.5 大文件索引分块

**场景：** 某个 active/ 文件非常大（> 100KB），包含大量 H2 章节。

**规则：**
- 每个 H2 chunk 的文本长度上限为 2000 tokens（约 8000 chars）
- 超过上限的 chunk 按段落继续拆分
- 一个 topic 的 embedding 索引文件 `.embedding_index/<topic>.jsonl` 最多 50 行
- 超过 50 行说明记忆过于庞大，应拆分为多个 topic

### 8.6 冷启动场景

**场景：** 新安装系统，active/ 为空，无索引数据。

**流程：**
1. `curve-memory-cli init` 创建目录结构和空的 ACTIVITY.yaml
2. 首次使用 `memory("add")` 创建记忆时，写入 active/
3. 首次 `curve-memory-cli index` 构建索引
4. 在索引构建完成前，检索降级为纯关键词匹配（level 3）

### 8.7 记忆重新激活后的索引重建

**场景：** `archive/forgotten/` 中的记忆被重新激活（mv 回 active/）。

**处理：**
1. agent 执行重新激活流程（mv back + 更新 ACTIVITY.yaml + 更新 MEMORY.md）
2. 下次 `curve-memory-indexer` cron 运行时检测到新文件
3. 重新构建该 topic 的 embedding 和 FTS5 索引
4. 如果 agent 在重新激活后立即需要检索该记忆，降级为仅 BM25 匹配 topic name

---

## 9. 回滚方案

### 9.1 Phase 1 回滚（回到 ADR-001 线性模型）

```bash
# 恢复备份
cp ~/.hermes/memories/ACTIVITY.yaml.bak ~/.hermes/memories/ACTIVITY.yaml
cp ~/.hermes/memories/MEMORY.md.bak ~/.hermes/memories/MEMORY.md

# 恢复旧 cron 脚本
cp ~/.hermes/scripts/memory-decay.py.bak ~/.hermes/scripts/memory-decay.py
rm ~/.hermes/scripts/memory-forgetting.py

# 恢复 crontab
crontab -e  # 替换 cron 命令为旧版

# 恢复目录结构（可选）
# 如果创建了 archive/ 目录，保留；active/ 和 archive/forgotten/ 中的文件手动合并
```

### 9.2 Phase 1→2 降级（去掉 embedding，回到纯遗忘曲线）

```bash
# 停用 indexer cron
# crontab -e 中注释掉 curve-memory-indexer.py

# 清理 embedding 目录（可选，节省空间）
rm -rf ~/.hermes/memories/.embedding_index/
rm -rf ~/.hermes/memories/.fts5/
rm ~/.hermes/memories/.embedding_meta.yaml

# 修改 ACTIVITY.yaml 中的 metadata.retrieval 字段
# 设置 alpha=1.0, beta=0.0, gamma=0.0

# 系统自动降级为关键字搜索（TIER 分级 + BM25 索引功能保留）
```

### 9.3 完全回滚到 ADR-005 之前的状态

```bash
#!/bin/bash
# rollback-to-adr001.sh

BACKUP_DIR=~/.hermes/backups/$(date +%Y%m%d_%H%M%S)

# 备份当前状态
mkdir -p $BACKUP_DIR/hermes
cp -r ~/.hermes/memories $BACKUP_DIR/hermes/
cp -r ~/.hermes/knowledge $BACKUP_DIR/hermes/
cp ~/.hermes/config.yaml $BACKUP_DIR/hermes/

# 如果有 ADR-001 的备份，直接恢复
if [ -f ~/.hermes/backups/pre-adr005/ACTIVITY.yaml ]; then
    cp ~/.hermes/backups/pre-adr005/* ~/.hermes/memories/
fi

# 清理 Plugin 注册
hermes plugins uninstall curve-memory 2>/dev/null || true

# 重置 embedding 索引
rm -rf ~/.hermes/memories/.embedding_index/
rm -rf ~/.hermes/memories/.fts5/

# 恢复旧 cron
crontab ~/.hermes/backups/pre-adr005/crontab.bak 2>/dev/null || true

echo "✅ 已回滚至 ADR-001 状态"
echo "📦 备份保存在 $BACKUP_DIR"
```

### 9.4 嵌入模型不可用时的降级（非回滚，运行时降级）

这是设计预期的运行降级，不是回滚。系统自动处理：

```python
# 在 provider.py 中
def _init_embedder(config):
    """初始化 embedding provider，失败时降级"""
    try:
        if config.get("provider") == "ollama":
            ...
        else:
            from sentence_transformers import SentenceTransformer
            self.embedder = SentenceTransformer(config.get("model", "all-MiniLM-L6-v2"))
            self.degradation_level = 0
    except ImportError:
        self.embedder = None
        self.degradation_level = 1  # BM25 + R(t)
        print("⚠️  sentence-transformers 未安装，降级为 BM25 + R(t)")
    except Exception as e:
        self.embedder = None
        self.degradation_level = 1
        print(f"⚠️  嵌入模型加载失败 ({e})，降级为 BM25 + R(t)")
```

### 9.5 回滚验证清单

| 检查项 | 命令 | 预期结果 |
|--------|------|---------|
| ACTIVITY.yaml 可读 | `python3 -c "import yaml; print(yaml.safe_load(open('~/.hermes/memories/ACTIVITY.yaml')))"` | 正常加载 |
| MEMORY.md 可读 | `head -5 ~/.hermes/memories/MEMORY.md` | 含 idx: 条目 |
| 旧 memory tool 工作 | `hermes memory` 输出 | 正常显示 |
| 旧 cron 工作 | `python3 ~/.hermes/scripts/memory-decay.py` | 无错误 |
| 无残留 embedding | `ls -la ~/.hermes/memories/.embedding_index/ 2>&1` | 目录不存在或为空 |

---

## 10. 分阶段实施计划

### 10.1 总体路线图

```
Phase 0: 准备与备份 ─ 30 分钟
Phase 1: 遗忘曲线系统落地 ─ 3 小时  ← 可独立交付
Phase 1.5: 系统稳定与验证 ─ 持续 1 周
Phase 2: 语义检索叠加 ─ 4 小时      ← 依赖 Phase 1
Phase 3: Plugin 封装 ─ 2 小时       ← 依赖 Phase 1 + 2
Phase 4: 集成联调 ─ 2 小时
Phase 5: 长期监控与调优 ─ 持续
```

**每个 Phase 可独立交付**——Phase 1 完成后系统即可正常使用，Phase 2+ 是增强层。

---

### 10.2 Phase 0: 准备与备份（30 分钟）

**目标：** 确保当前系统可回滚，确认目录结构完整。

```bash
# 备份当前系统
cp ~/.hermes/memories/ACTIVITY.yaml ~/.hermes/backups/pre-adr005/ACTIVITY.yaml
cp ~/.hermes/memories/MEMORY.md ~/.hermes/backups/pre-adr005/MEMORY.md
cp -r ~/.hermes/scripts/ ~/.hermes/backups/pre-adr005/scripts/
crontab -l > ~/.hermes/backups/pre-adr005/crontab.bak

# 确认目录结构
ls -la ~/.hermes/memories/
ls -la ~/.hermes/memories/archive/forgotten/ 2>/dev/null || echo "目录不存在，后续创建"
ls -la ~/.hermes/knowledge/ 2>/dev/null || echo "目录不存在，后续创建"
```

**检查清单：**
- [ ] 备份当前 ACTIVITY.yaml、MEMORY.md、脚本
- [ ] 备份 crontab 配置
- [ ] 确认 Hermes 版本兼容性
- [ ] 记录当前活跃记忆数量（用于回滚验证）

---

### 10.3 Phase 1: 遗忘曲线系统（3 小时）

**目标：** 替换对数温度模型为遗忘曲线模型，实现 TIER 分级、双层归档、cron 衰减。

**这是整个系统的基础——Phase 1 完成后系统即可日常使用。**

#### 子任务 1.1: 数据迁移（1 小时）

- [ ] 编写 `scripts/migrate-v2-to-v3.py`（v2 ACTIVITY.yaml → v3 格式）
  - [ ] metadata.model → "forgetting_curve"
  - [ ] 字段 x → t
  - [ ] 新增 metadata.formula、baseline、archive_threshold_t
  - [ ] 处理 t ∈ [30, 48) 的记忆：立即归档或保留
  - [ ] memory-system 设定 protected: true
- [ ] 执行迁移
  ```bash
  python3 ~/.hermes/scripts/migrate-v2-to-v3.py
  ```
- [ ] 验证输出：`python3 -c "import yaml; d=yaml.safe_load(open('~/.hermes/memories/ACTIVITY.yaml')); print(d['metadata']['model'])"`
- [ ] 输出应显示 `forgetting_curve`
- [ ] 可选：更新 MEMORY.md 中 `[x=N]` → `[t=N]`

#### 子任务 1.2: 实现 R(t) 引擎 + TIER 模块（1 小时）

- [ ] 编写 `~/.hermes/scripts/tier.py`（作为公共模块）：
  - [ ] `forgetting_curve(t: int) -> float`
  - [ ] `r_to_tier(r: float) -> str`
  - [ ] `r_to_tier_level(r: float) -> int`（返回 1-5 的数值，供排序用）
- [ ] 单元测试（内置在 tier.py 的 `if __name__ == "__main__"` 块中）：
  ```bash
  # 验证关键点
  python3 -c "
  from tier import forgetting_curve, r_to_tier
  assert abs(forgetting_curve(0) - 1.0) < 0.001
  assert abs(forgetting_curve(30) - 0.4628) < 0.01
  assert abs(forgetting_curve(60) - 0.4620) < 0.01
  assert r_to_tier(0.9) == 'TIER_5 🔥'
  assert r_to_tier(0.5) == 'TIER_3 📙'
  print('All tests passed')
  "
  ```
- [ ] ralqlator 兼容公式验证

#### 子任务 1.3: 实现 cron 衰减脚本（1 小时）

- [ ] 编写 `~/.hermes/scripts/curve-memory-forgetting.py`：
  - [ ] 读取 ACTIVITY.yaml（v3 格式）
  - [ ] 所有记忆 t += 1（protected 除外）
  - [ ] 计算 R(t) → TIER 映射
  - [ ] 成熟度检测（access_count ≥ 20 且 t ≤ 3 → mature=true）
  - [ ] 归档判定（t ≥ 30 → 遗忘归档 or 成熟归档）
  - [ ] 文件锁（.memory.lock）防止竞态
  - [ ] FORGET_LOG.md 写入
  - [ ] 写回 ACTIVITY.yaml
- [ ] 手动测试：
  ```bash
  python3 ~/.hermes/scripts/curve-memory-forgetting.py
  # 检查输出日志和 ACTIVITY.yaml 更新
  ```

#### 子任务 1.4: 更新 cron 配置（15 分钟）

```bash
crontab -e
# 替换：
# 旧：0 3 * * * cd ~/.hermes && python3 scripts/memory-temperature.py
# 新：0 3 * * * cd ~/.hermes && python3 scripts/curve-memory-forgetting.py
```

#### 子任务 1.5: 更新 Agent 协议文档（30 分钟）

- [ ] 重写 `~/.hermes/memories/active/memory-system.md`：
  - [ ] R(t) = 0.462 + 0.538 * exp(-t/2.71) + ralqlator 版
  - [ ] TIER 映射表（六级，含详细度规范）
  - [ ] 每次对话时的流程：解析 idx → 计算 R(t) → 按 TIER 读 → 更新 ACTIVITY.yaml
  - [ ] 温度回弹时的内容重建指引
  - [ ] 双层归档说明
  - [ ] memory-system 标记 protected: true

#### Phase 1 交付标准

- [ ] `curve-memory-forgetting.py` 每日 03:00 运行无报错
- [ ] ACTIVITY.yaml v3 格式正确，与旧版 memory tool 兼容
- [ ] agent 能根据 R(t) 决定读取深度
- [ ] 受保护记忆（memory-system）不走衰
- [ ] 遗忘归档和成熟归档正常工作
- [ ] FORGET_LOG.md 记录正确
- [ ] 回归：旧 `idx:topic [x=N]` 格式仍可解析

---

### 10.4 Phase 1.5: 系统稳定与验证（持续 1 周）

**目标：** 在日常使用中验证遗忘曲线系统，发现和修复问题后再叠加语义检索。

- [ ] 每日检查 cron 日志是否正常执行
- [ ] 验证归档频率是否合理（新阈值 30 天 vs 旧阈值 48 天）
- [ ] 监控 TIER 分布变化
- [ ] 收集 agent 反馈：R(t) 驱动的读取深度是否合适
- [ ] 按需微调 TIER 映射边界（如发现 TIER_2 → TIER_1 转换太突然）
- [ ] 检查成熟度判定参数是否合理（access_count ≥ 20 是否太低/太高）
- [ ] 修复 Phase 1 中发现的 bug

**稳定性通过标准（进入 Phase 2 的前提）：**
- [ ] 连续 3 天 cron 无报错
- [ ] 无意外归档事件
- [ ] agent 正常工作且用户满意
- [ ] 归档/重新激活流程正常

---

### 10.5 Phase 2: 语义检索叠加（4 小时）

**目标：** 在 Phase 1 遗忘曲线系统基础上，增加 BM25 全文索引和 embedding 语义检索。

#### 子任务 2.1: 实现 Chunk 分割模块（30 分钟）

- [ ] 编写 `chunker.py`：
  - [ ] 按 `##`（H2）标题分割
  - [ ] 每个 chunk 上限 2000 tokens（约 8000 chars）
  - [ ] 返回 `list[dict{topic, chunk, text, mtime}]`
- [ ] 测试：`python3 ~/.hermes/scripts/chunker.py --test`

#### 子任务 2.2: 实现 Embedding Provider 抽象（30 分钟）

- [ ] 编写 `embedding_provider.py`：
  - [ ] `EmbeddingProvider` 抽象基类
  - [ ] `SentenceTransformersProvider` 实现
  - [ ] `OllamaProvider` 实现
  - [ ] Provider 工厂函数（根据 config 决定）
  - [ ] 懒加载：只有在构建/更新索引时才加载模型
  - [ ] 加载失败时打印告警并返回 `None`

#### 子任务 2.3: 实现 Indexer（1.5 小时）

- [ ] 编写 `curve-memory-indexer.py`：
  - [ ] 扫描 `active/*.md` 的 mtime
  - [ ] 变动的文件 → chunk → embed → 写入 `.embedding_index/<topic>.jsonl`
  - [ ] 构建 SQLite FTS5 表（`.fts5/curve_memory_fts5.db`）
  - [ ] 增量和全量两种模式（`--incremental` / `--rebuild`）
  - [ ] 归档时清理对应的 embedding 和 FTS5 索引
  - [ ] 更新 `.embedding_meta.yaml`
  - [ ] 文件锁（`.memory.lock`）与 forgetting cron 互斥
- [ ] 手动测试：
  ```bash
  python3 ~/.hermes/scripts/curve-memory-indexer.py --rebuild
  # 检查 .embedding_index/ 和 .fts5/ 目录
  ```

#### 子任务 2.4: 实现 FTS5 检索 + Embedding 检索（30 分钟）

- [ ] 编写 `search.py`：
  - [ ] `fts5_search(query, db_path) → dict[topic, bm25_score]`
  - [ ] `semantic_search(query, embedder, index_dir) → dict[topic, cosine_score]`
  - [ ] `hybrid_fuse(bm25_scores, cosine_scores, r_values, alpha, beta, gamma) → list[(topic, score, snippet)]`
  - [ ] 归一化逻辑（min-max for BM25, [0,1] clamp for cosine, linear for R(t)）
  - [ ] 输出 top-5 topics + R(t) + snippet

#### 子任务 2.5: 实现曲线 × 检索关联规则（30 分钟）

- [ ] 在 indexer 中实现 TIER × 检索关联规则：
  - [ ] TIER_5、TIER_4：全量索引（BM25 + embedding）
  - [ ] TIER_3：摘要索引（仅摘要句的 embedding）
  - [ ] TIER_2：仅 BM25 摘要索引
  - [ ] TIER_1：仅 topic name 索引
  - [ ] ARCHIVE：清除所有索引
  - [ ] 每次 TIER 降级时触发索引裁剪

#### 子任务 2.6: 实现降级链（30 分钟）

- [ ] 在 search.py 中实现五级降级链
- [ ] 降级检测逻辑：
  - [ ] 检查 sentence-transformers 是否可用
  - [ ] 检查 FTS5 数据库是否存在
  - [ ] 检查 .embedding_index/ 是否有数据
- [ ] 降级日志：每次检索后记录当前降级级别

#### 子任务 2.7: 添加 indexer 到 cron（15 分钟）

```bash
crontab -e
# 添加：
45 3 * * * cd ~/.hermes && python3 scripts/curve-memory-indexer.py --incremental 2>> ~/.hermes/logs/indexer.log
```

#### Phase 2 交付标准

- [ ] `curve-memory-indexer.py` 对现有 active/ 文件构建索引
- [ ] 三路检索返回正确结果
- [ ] 五级降级链每级行为正确
- [ ] 纯 BM25 + R(t) 降级工作时系统正常
- [ ] TIER 降级时索引自动裁剪
- [ ] 索引尺寸合理（500 条记忆 < 10 MB）

---

### 10.6 Phase 3: Plugin 封装（2 小时）

**目标：** 将遗忘曲线系统 + 语义检索封装为 Hermes Plugin，提供 CLI 和 Provider。

#### 子任务 3.1: 创建 Plugin 目录结构（15 分钟）

```bash
mkdir -p ~/.hermes/hermes-agent/plugins/memory/curve-memory/
mkdir -p ~/.hermes/hermes-agent/plugins/memory/curve-memory/scripts/
mkdir -p ~/.hermes/hermes-agent/plugins/memory/curve-memory/skill/
```

#### 子任务 3.2: 重构代码为模块（1 小时）

将 Phase 1 + 2 的脚本重构为 Plugin 模块：

| 源文件 | 目标路径 |
|--------|---------|
| `~/.hermes/scripts/tier.py` | `plugins/memory/curve-memory/tier.py` |
| `~/.hermes/scripts/chunker.py` | `plugins/memory/curve-memory/chunker.py` |
| `~/.hermes/scripts/search.py` | `plugins/memory/curve-memory/search.py` |
| `~/.hermes/scripts/embedding_provider.py` | `plugins/memory/curve-memory/embedder.py` |
| `~/.hermes/scripts/curve-memory-forgetting.py` | `plugins/memory/curve-memory/scripts/forgetting.py` |
| `~/.hermes/scripts/curve-memory-indexer.py` | `plugins/memory/curve-memory/scripts/indexer.py` |

#### 子任务 3.3: 实现 CurveMemoryProvider（30 分钟）

- [ ] 编写 `plugins/memory/curve-memory/provider.py`：
  - [ ] `is_available()` — 检查 ACTIVITY.yaml 是否存在
  - [ ] `initialize()` — 加载配置、embedding provider、FTS5 连接、ACTIVITY.yaml 缓存
  - [ ] `prefetch()` — 三路检索 + TIER 注入
  - [ ] `sync_turn()` — 更新 touch 过的记忆
  - [ ] `get_tool_schemas()` — 注册 `curve_memory_semantic_search` 工具
  - [ ] `system_prompt_block()` — 注入活跃记忆概览

#### 子任务 3.4: 实现 CLI（30 分钟）

- [ ] 编写 `plugins/memory/curve-memory/cli.py`：
  - [ ] `init` — 创建目录结构 + 初始化 ACTIVITY.yaml
  - [ ] `search` — 三路检索（human-readable + JSON 输出）
  - [ ] `read` — 按 TIER 读文件
  - [ ] `touch` — 更新 ACTIVITY.yaml
  - [ ] `status` — 活跃记忆概览 + TIER 分布 + 索引状态 + 降级级别
  - [ ] `daily-tick` — 手动触发每日衰减
  - [ ] `index` — 手动触发索引更新
  - [ ] `forget` — 手动归档
  - [ ] `mature` — 手动标记成熟
  - [ ] `check` — 健康检查

#### 子任务 3.5: 编写 Skill 文档（15 分钟）

- [ ] 编写 `plugins/memory/curve-memory/skill/SKILL.md`：
  - [ ] R(t) 公式（含 ralqlator 版本）
  - [ ] TIER 映射表
  - [ ] 三路检索说明
  - [ ] agent 行为协议（读取深度规则、内容重建规则、归档规则）

#### 子任务 3.6: 编写 __init__.py（15 分钟）

- [ ] 编写 `plugins/memory/curve-memory/__init__.py`：
  - [ ] 注册 `CurveMemoryProvider`
  - [ ] 注册 CLI 子命令
  - [ ] 注册 cron 任务
  - [ ] 注册 tool `curve_memory_semantic_search`

#### Phase 3 交付标准

- [ ] `hermes plugins install curve-memory` 成功
- [ ] `curve-memory-cli search "query"` 返回正确结果
- [ ] `curve-memory-cli status` 显示完整概览
- [ ] Provider 的 `prefetch()` 在对话中注入相关记忆
- [ ] Provider 的 `sync_turn()` 正确更新 t 值
- [ ] 跨 profile 共享目录测试通过

---

### 10.7 Phase 4: 集成联调（2 小时）

**目标：** 全链路端到端验证，确保 Phase 1→3 所有组件协同工作。

#### 子任务 4.1: 端到端测试

```bash
# 1. 初始化
hermes curve-memory-cli init

# 2. 添加记忆（模拟 memory tool）
echo "# workflow — 测试" > ~/.hermes/memories/active/workflow.md
echo "# rust-lifetimes — 测试" > ~/.hermes/memories/active/rust-lifetimes.md
# 添加 idx 到 MEMORY.md
echo "idx:workflow [t=0] → active/workflow.md" >> ~/.hermes/memories/MEMORY.md
echo "idx:rust-lifetimes [t=0] → active/rust-lifetimes.md" >> ~/.hermes/memories/MEMORY.md

# 3. 构建索引
hermes curve-memory-cli index --rebuild

# 4. 检索测试
hermes curve-memory-cli search "borrow checker"
# 预期：能检索到 rust-lifetimes（语义匹配）

# 5. 运行一日衰减
hermes curve-memory-cli daily-tick

# 6. 手动归档
hermes curve-memory-cli forget test-topic

# 7. 状态查看
hermes curve-memory-cli status
```

#### 子任务 4.2: 降级链验证

```bash
# Level 0 → Level 1
pip uninstall sentence-transformers -y
hermes curve-memory-cli check
# 预期：显示 degrade_level=1, 仅 BM25 + R(t)

# Level 1 → Level 3
mv ~/.hermes/memories/.fts5/curve_memory_fts5.db ~/.hermes/memories/.fts5/curve_memory_fts5.db.bak
hermes curve-memory-cli check
# 预期：显示 degrade_level=3, 仅 topic name 匹配

# 恢复
mv ~/.hermes/memories/.fts5/curve_memory_fts5.db.bak ~/.hermes/memories/.fts5/curve_memory_fts5.db
pip install sentence-transformers
hermes curve-memory-cli index --rebuild
hermes curve-memory-cli check
# 预期：显示 degrade_level=0, 三路全开
```

#### 子任务 4.3: 竞态条件测试

```bash
# 同时运行 forgetting 和 indexer
python3 ~/.hermes/scripts/curve-memory-forgetting.py &
python3 ~/.hermes/scripts/curve-memory-indexer.py --incremental &
wait
# 检查 .memory.lock 已释放
# 检查 ACTIVITY.yaml 和索引一致
```

#### 子任务 4.4: 性能测试

```bash
# 模拟 500 条记忆
for i in $(seq 1 500); do
    echo "# topic-$i — 测试内容" > ~/.hermes/memories/active/topic-$i.md
done
# 全量构建索引
time hermes curve-memory-cli index --rebuild
# 预期：500 条 < 30 秒

# 检索测试
time hermes curve-memory-cli search "test content" --json
# 预期：< 50ms
```

#### Phase 4 交付标准

- [ ] 端到端全链路工作：记忆创建 → 索引 → 检索 → 读取 → 衰减 → 归档
- [ ] 五级降级链每级行为正确
- [ ] 竞态条件测试通过
- [ ] 500 条记忆性能测试通过
- [ ] 回滚方案已验证

---

### 10.8 Phase 5: 长期监控与调优（持续）

**目标：** 在实际使用中优化参数和索引策略。

- [ ] 每周检查 `curve-memory-cli status` 中的 TIER 分布
- [ ] 每月检查 FORGET_LOG.md 的归档统计
- [ ] 如果知识库 `knowledge/` 增长过快，考虑引入 knowledge/ 的索引机制
- [ ] 按需调整权重参数（α, β, γ）
- [ ] 按需调整 TIER 映射边界
- [ ] 按需调整成熟度判定参数（access_count 阈值）
- [ ] 如果 embedding 索引增长超过 50 MB，考虑精简 chunk 策略
- [ ] 如果 BM25 召回率下降，检查 FTS5 是否需要重建

**参数调优指引：**

| 现象 | 可能的调整 | 风险 |
|------|-----------|------|
| 检索结果太偏重关键词 | 降低 α，提高 β | 语义无结果时不相关 |
| 检索结果太偏重新记忆 | 降低 γ，提高 α 或 β | 陈旧但重要的记忆被忽略 |
| 归档太快 | 提高 archive_threshold_t | 内存增长 |
| 归档太慢 | 降低 archive_threshold_t | 低价值记忆占用空间 |
| 成熟度标记太快 | 提高 MATURE_ACCESS_THRESHOLD | 核心知识未固化 |
| 成熟度标记太慢 | 降低 MATURE_ACCESS_THRESHOLD | 知识库增长过快 |

---

## 11. 附录：关键代码骨架

### 11.1 R(t) 计算 + TIER 映射

```python
# tier.py — 遗忘曲线计算和 TIER 映射
import math

TAU = 2.71
BASELINE = 0.462

def forgetting_curve(t: int) -> float:
    """R(t) = 0.462 + 0.538 * exp(-t/2.71)"""
    if t == 0:
        return 1.0
    return max(BASELINE, BASELINE + (1 - BASELINE) * math.exp(-t / TAU))

def r_to_tier(r: float) -> str:
    """R(t) → TIER 等级字符串"""
    if r >= 0.800:   return "TIER_5 🔥"
    if r >= 0.640:   return "TIER_4 📗"
    if r >= 0.503:   return "TIER_3 📙"
    if r >= 0.465:   return "TIER_2 📕"
    if r > BASELINE: return "TIER_1 📦"
    return "ARCHIVE 🗄️"

def r_to_tier_level(r: float) -> int:
    """R(t) → TIER 等级数值（5=最新, 0=归档）"""
    if r >= 0.800:   return 5
    if r >= 0.640:   return 4
    if r >= 0.503:   return 3
    if r >= 0.465:   return 2
    if r > BASELINE: return 1
    return 0

def is_archive(t: int, protected: bool = False) -> bool:
    """是否达到归档条件"""
    if protected:
        return False
    return t >= 30
```

### 11.2 三路检索融合

```python
# search.py — 三路混合检索核心
import sqlite3
import json
import numpy as np
from pathlib import Path
from typing import Optional

DEFAULT_ALPHA = 0.35  # BM25
DEFAULT_BETA = 0.45   # Cosine
DEFAULT_GAMMA = 0.20  # R(t)

def hybrid_search(query: str,
                  activity_cache: dict,
                  fts5_path: Path,
                  embedding_index_dir: Path,
                  embedder: Optional[object] = None,
                  alpha: float = DEFAULT_ALPHA,
                  beta: float = DEFAULT_BETA,
                  gamma: float = DEFAULT_GAMMA,
                  top_k: int = 5) -> list[dict]:
    """
    三路混合检索
    返回: [{"topic": str, "score": float, "r": float, "snippet": str, "tier": str}, ...]
    """
    # 1. FTS5 BM25
    bm25_scores = _fts5_search(query, fts5_path) if fts5_path.exists() else {}

    # 2. Embedding cosine similarity
    cosine_scores = {}
    if embedder is not None:
        cosine_scores = _semantic_search(query, embedder, embedding_index_dir)

    # 3. R(t) values
    r_values = {}
    for topic, info in activity_cache.get("memories", {}).items():
        t = info.get("t", 0)
        r_values[topic] = forgetting_curve(t)

    # 4. 归一化
    bm25_norm = _normalize_minmax(bm25_scores)
    cosine_norm = _normalize_minmax(cosine_scores)
    r_norm = {k: (v - 0.462) / 0.538 for k, v in r_values.items()}

    # 5. 加权融合
    all_topics = set(bm25_norm) | set(cosine_norm) | set(r_norm)
    final = []
    for topic in all_topics:
        score = (alpha * bm25_norm.get(topic, 0.0) +
                 beta * cosine_norm.get(topic, 0.0) +
                 gamma * r_norm.get(topic, 0.0))
        r = r_values.get(topic, 0.462)
        final.append({
            "topic": topic,
            "score": round(score, 4),
            "r": round(r, 4),
            "tier": r_to_tier(r),
        })

    # 6. 排序 + top-K
    final.sort(key=lambda x: x["score"], reverse=True)
    return final[:top_k]

def _fts5_search(query: str, db_path: Path) -> dict[str, float]:
    """SQLite FTS5 BM25 检索"""
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute(
        "SELECT topic, rank FROM fts_memories WHERE fts_memories MATCH ? ORDER BY rank LIMIT 20",
        (query,)
    )
    results = {row[0]: -row[1] for row in cursor.fetchall()}
    conn.close()
    return results

def _semantic_search(query: str, embedder, index_dir: Path) -> dict[str, float]:
    """Cosine similarity search over .embedding_index/*.jsonl"""
    q_vec = np.array(embedder.embed(query))
    results = {}
    for jsonl_file in index_dir.glob("*.jsonl"):
        topic = jsonl_file.stem
        with open(jsonl_file, "r") as f:
            for line in f:
                entry = json.loads(line)
                chunk_vec = np.array(entry["vector"])
                sim = np.dot(q_vec, chunk_vec) / (
                    np.linalg.norm(q_vec) * np.linalg.norm(chunk_vec) + 1e-10
                )
                results[topic] = max(results.get(topic, 0.0), float(sim))
    return results

def _normalize_minmax(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    vals = list(scores.values())
    mn, mx = min(vals), max(vals)
    span = mx - mn if mx > mn else 1.0
    return {k: (v - mn) / span for k, v in scores.items()}
```

### 11.3 Indexer 核心

```python
# indexer.py — Embedding + FTS5 索引管理
import hashlib
import json
import sqlite3
from pathlib import Path

MEMORIES_DIR = Path.home() / ".hermes" / "memories"
ACTIVE_DIR = MEMORIES_DIR / "active"
INDEX_DIR = MEMORIES_DIR / ".embedding_index"
FTS5_DIR = MEMORIES_DIR / ".fts5"
META_FILE = MEMORIES_DIR / ".embedding_meta.yaml"

def build_indexes(embedder, rebuild: bool = False):
    """构建/更新 embedding 和 FTS5 索引"""
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    FTS5_DIR.mkdir(parents=True, exist_ok=True)

    # FTS5 连接
    conn = _init_fts5()

    for md_file in sorted(ACTIVE_DIR.glob("*.md")):
        topic = md_file.stem
        content = md_file.read_text(encoding="utf-8")
        file_hash = hashlib.md5(content.encode()).hexdigest()

        # 增量跳过
        if not rebuild and _is_indexed(topic, file_hash):
            continue

        # 分 chunk
        chunks = chunk_markdown(topic, content)
        # Embedding
        if embedder:
            texts = [c["text"] for c in chunks]
            vectors = embedder.embed_batch(texts)
            _write_jsonl(topic, chunks, vectors)

        # FTS5
        _update_fts5(conn, topic, content)

    conn.commit()
    conn.close()
    _update_meta(embedder)

def chunk_markdown(topic: str, content: str) -> list[dict]:
    """按 H2 章节分割 Markdown"""
    lines = content.split("\n")
    chunks = []
    current_section = "概要"
    current_lines = []

    for line in lines:
        if line.startswith("## "):
            if current_lines:
                chunks.append({
                    "topic": topic,
                    "chunk": current_section,
                    "text": "\n".join(current_lines).strip()
                })
            current_section = line[3:].strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        chunks.append({
            "topic": topic,
            "chunk": current_section,
            "text": "\n".join(current_lines).strip()
        })

    return chunks
```

### 11.4 全链路延时分解

```
用户发送消息
  │
  0ms    ├── FTS5 BM25 检索     ← SQLite 全文索引，< 5ms
  5ms    ├── embed(query)       ← CPU 30ms (all-MiniLM-L6-v2)
 35ms    ├── cosine_sim 扫描     ← 1000 chunk × 384 维 < 10ms
 45ms    ├── 三路归一化 + 融合    ← 纯数学，< 1ms
 46ms    ├── 查 ACTIVITY.yaml   ← 内存缓存，< 1ms
 47ms    ├── TIER 判定 + read   ← 文件 I/O，< 1ms
 48ms    └── 注入 system prompt
         └── LLM 推理（数秒，非本系统负责）
```

**检索总耗时约 48ms**（纯检索阶段，不含 LLM 推理）。

### 11.5 运维成本评估

#### 磁盘占用

| 组件 | 500 条记忆 | 2000 条记忆 | 说明 |
|------|-----------|------------|------|
| active/*.md | ~5 MB | ~20 MB | 每条约 10KB 估算 |
| .embedding_index/*.jsonl | ~4 MB | ~16 MB | 每条 3-5 chunk，384 dim x 4 bytes |
| .fts5/curve_memory_fts5.db | ~1 MB | ~4 MB | FTS5 压缩索引 |
| .embedding_meta.yaml | < 1 KB | < 1 KB | 固定大小 |
| archive/ | 变量 | 变量 | 取决于归档策略 |
| 合计 | ~10 MB | ~40 MB | 远低于 1GB 阈值 |

#### CPU / 内存

| 组件 | 内存 | CPU | 触发时机 |
|------|------|-----|---------|
| sentence-transformers 模型 | ~100 MB（加载后） | ~30ms/query | 首次 index + 每次 search |
| FTS5 SQLite | < 5 MB | < 5ms/query | 每次 search |
| ACTIVITY.yaml 缓存 | < 1 MB | < 1ms/lookup | 常驻内存 |
| 模型 pip 依赖 | ~300 MB（pip install） | — | 首次安装 |

**关键结论：** 引入语义检索的额外运维成本约 **100 MB 内存 + 30MB 磁盘 + 每次检索 30ms 额外延迟**。本地模型无 API 费用，无网络依赖。

#### 首次安装步骤

```bash
# 核心依赖（Phase 1 就需要）
pip install pyyaml

# 语义检索依赖（Phase 2 需要）
pip install sentence-transformers numpy

# 可选（Ollama 用户）
# pip install requests
# 提前 pull 模型：ollama pull nomic-embed-text
```

---

## 变更记录

| 版本 | 日期 | 变更内容 |
|------|------|---------|
| v1.0 | 2026-06-01 | 初始版本：融合 ADR-003 (遗忘曲线) + ADR-004 (语义检索 & Plugin) |
