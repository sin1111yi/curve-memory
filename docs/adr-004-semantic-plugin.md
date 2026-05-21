# ADR-004: 语义检索 + Plugin 封装（基于 ADR-003 遗忘曲线）

**状态：** 📐 设计完成（待实现）
**时间：** 2026-06-01
**扩展：** ADR-003 (ADR-memory-forgetting-curve.md)
**依赖：** ADR-001 ~ ADR-003

---

## 上下文

ADR-003（遗忘曲线记忆系统）解决了记忆的**衰减和内容分级**问题，但检索方式仍是纯关键词匹配——agent 通过 `idx:topic` 索引条目，依赖 topic name 与用户查询的关键词重叠。当用户说"borrow checker 报错"而索引条目是 `idx:rust-lifetimes` 时，当前设计无法命中。

同时，ADR-003 的设计需要配套的**系统组件**（cron 脚本、目录管理、YAML 读写），目前这些组件只是作为 Python 脚本散落在 `~/.hermes/scripts/` 中。如果将这套系统复用到其他 agent（Claude Code、Codex、同一 Hermes 的不同 profile），需要思考封装形式。

本 ADR 回答两个问题：
1. 如何在 ADR-003 之上增加语义匹配能力
2. 整套系统应以什么形式封装以便复用

---

## 决策一：三路混合检索

在 ADR-003 的三层结构之上，增加三路加权混合检索——**从第一天就做完整方案，跳过渐进式方案二**。

### 1.1 核心公式

```
最终排序分 = α · BM25_score + β · cosine_sim + γ · R(t)

其中：
  BM25_score    — FTS5 精确关键词匹配
  cosine_sim    — embedding 语义相似度
  R(t)          — 遗忘曲线保留率（ACTIVITY.yaml）
  α, β, γ       — 可调权重，默认 α=0.35, β=0.45, γ=0.20
```

**权重默认值的理由：**

| 权重 | 值 | 理由 |
|------|----|------|
| α = 0.35 | BM25 关键词分 | 用户常用精确术语（"ACTIVITY.yaml"、"R(t)"），关键词精确匹配不可替代 |
| β = 0.45 | embedding 语义分 | 最高权重——语义匹配覆盖关键词盲区，是整个设计的核心增益 |
| γ = 0.20 | R(t) 新鲜度分 | 保底权重——太久远的记忆即使语义匹配再高也不应冲顶；但不设过高以免新记忆总是压倒一切 |

### 1.2 架构

```
用户消息 → query
  │
  ├──→ FTS5 (SQLite)
  │      ↓
  │      BM25_score  ← 对 active/*.md 的全文索引
  │
  ├──→ sentence-transformers all-MiniLM-L6-v2
  │      ↓
  │      cosine_sim(.embedding_index/*.jsonl)  ← 按 chunk 级内容
  │
  └──→ 查 ACTIVITY.yaml
         ↓
         R(t) = 0.462 + 0.538 · exp(-t/2.71)
  │
  └──→ 三路归一化 + 加权融合
         ↓
         top-K topics
         ↓
         查 ACTIVITY.yaml 的 t → R(t) → TIER 映射
         ↓
         read_file(active/<topic>.md, depth=TIER)
         ↓
         注入 system prompt
         ↓
         agent 回复后 touch topic（t=0, access_count++）
```

**三路检索并行执行，融合后才排序——不串行，不互相等待。**

### 1.3 数据流

```
写入路径：
  agent 写 active/<topic>.md
    → curve-memory-indexer.py 检测到新文件（cron 或 inotify 触发）
    → 按 ## 章节标题分 chunk
    → sentence-transformers → 384 维向量
    → 追加到 .embedding_index/<topic>.jsonl
    → 更新 SQLite FTS5 全文索引（覆盖 active/*.md）

检索路径：
  query 进入
    → FTS5：BM25 排序 → 归一化得分
    → embed(query) → cosine_sim 扫描 .embedding_index/*.jsonl → 归一化得分
    → 查 ACTIVITY.yaml → R(t) → 归一化得分
    → score = 0.35·BM25 + 0.45·cosine + 0.20·R(t)
    → top-5 topics
    → 按 TIER 缩减内容
    → 注入

索引更新路径（cron 每日）：
  检测 active/*.md 的 mtime
    → 变动的文件 re-chunk → re-embed → 替换 .jsonl + 更新 FTS5
    → 归档的文件 → 清理 .embedding_index/ + 清理 FTS5
```

### 1.4 目录结构增量

```
~/.hermes/memories/
├── ...（ADR-003 全部结构不变）
├── .embedding_index/         ← chunk → vector 映射，纯机器数据
│   ├── workflow.jsonl        ← 每个 chunk 一行 JSON
│   ├── rust-lifetimes.jsonl
│   └── ...
├── .embedding_meta.yaml      ← 模型、版本、维度等信息
└── .fts5/                    ← SQLite FTS5 索引文件
    └── curve_memory_fts5.db        ← 对 active/*.md 的全文索引
```

### 1.5 Chunk 策略

按 Markdown 章节标题分割，每个 `## <标题>` 段落为一个 chunk：

```jsonl
{"topic": "rust-lifetimes", "chunk": "核心事实", "text": "生命周期标注用 'a 语法，函数签名中...", "mtime": "2026-05-20", "vector": [0.12, -0.34, ...]}
{"topic": "rust-lifetimes", "chunk": "常见错误", "text": "E0495: 返回值需要显式生命周期...", "mtime": "2026-05-20", "vector": [...]}
```

**选择 `##`（H2）而非其他粒度的原因：**

| 粒度 | 优点 | 缺点 |
|------|------|------|
| H1（整篇文档） | 文件少 | 粒度太粗，长篇记忆只有一个向量 |
| **H2（章节）** | 粒度适中，语义集中 | — |
| 段落 / 多句 | 精度最高 | 索引膨胀，搜索延迟上升 |

### 1.6 模型选择

| 模型 | 维度 | CPU 延时 | 质量 |
|------|------|---------|------|
| **all-MiniLM-L6-v2** | 384 | ~30ms | 足够 — MTEB 平均 59.0 |
| all-mpnet-base-v2 | 768 | ~80ms | 更好 — MTEB 平均 62.3 |
| text-embedding-3-small (API) | 512 | ~200ms | 最好 — 但需要网络 |

**选择 all-MiniLM-L6-v2 为默认模型，同时设计可配置的 embedding provider 机制。**

| 维度 | 值 |
|------|-----|
| 延迟 | ~30ms（CPU 单次推理） |
| 内存 | ~100 MB（模型加载后） |
| 依赖 | `pip install sentence-transformers` |
| 磁盘 | ~80 MB（模型缓存至 `~/.cache/huggingface/`） |

模型默认使用 sentence-transformers 内联加载——进程内推理，无网络请求，零运维。

### 1.6.1 可配置的 Embedding Provider

允许用户切换其他本地 embedding 服务，不依赖外网 API。

```yaml
# ~/.hermes/config.yaml
memory:
  plugin: curve-memory
  embedding:
    provider: sentence-transformers   # 默认
    model: all-MiniLM-L6-v2           # sentence-transformers 支持的任意模型名
```

**可选 provider：**

| provider | 模型示例 | 维度 | 延迟 | 前提条件 |
|----------|---------|------|------|---------|
| `sentence-transformers` ✱ | `all-MiniLM-L6-v2` | 384 | ~30ms | `pip install sentence-transformers` |
| `sentence-transformers` | `all-mpnet-base-v2` | 768 | ~80ms | 同上 |
| `ollama` | `nomic-embed-text` | 768 | ~50-100ms | 已安装并运行 Ollama |
| `ollama` | `mxbai-embed-large` | 1024 | ~80-150ms | 已安装并运行 Ollama |

✱ 默认值

**ollama 的配置示例：**

```yaml
memory:
  plugin: curve-memory
  embedding:
    provider: ollama
    model: nomic-embed-text
    base_url: http://localhost:11434   # Ollama 默认地址
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

    def embed(self, text):
        return self.model.encode(text).tolist()

    def embed_batch(self, texts):
        return self.model.encode(texts).tolist()

class OllamaProvider(EmbeddingProvider):
    def __init__(self, model="nomic-embed-text", base_url="http://localhost:11434"):
        self.model = model
        self.base_url = base_url

    def embed(self, text):
        resp = requests.post(f"{self.base_url}/api/embeddings",
            json={"model": self.model, "prompt": text})
        return resp.json()["embedding"]

    def embed_batch(self, texts):
        return [self.embed(t) for t in texts]  # Ollama 无原生 batch
```

**设计要点：**
- Provider 在 `CurveMemoryProvider.initialize()` 时根据 config 实例化
- 如果配置的 provider 不可用（Ollama 未运行、模型未 pull），**不阻断启动**，自动降级为 BM25 + R(t)，并打印告警
- 所有 provider 的输出归一化为 `list[float]`，无论内部维度——`.embedding_index/` 中的向量直接存原始维度，cosine_sim 自动适配

### 1.7 降级链（五级）

```
三路全开（完全体）
  ↓ sentence-transformers 未安装
两路：BM25 + R(t)（效果等价于带新鲜度排序的关键词搜索）
  ↓ FTS5 索引损坏
两路：cosine + R(t)（纯语义 + 新鲜度）
  ↓ embedding + FTS5 均不可用
单路：R(t) 排序（仅按新鲜度，纯关键词匹配 topic name）
  ↓ 全不可用（系统恢复默认）
纯关键词匹配（ADR-003 原始设计，无 embedding，无 FTS5）
```

每一级降级不影响 agent 的调用接口——`curve-memory-cli search` 始终返回相同格式的结果。

---

## 决策二：Plugin 封装

将遗忘曲线系统 + 三路混合检索封装为一个 Hermes Plugin，以便复用。

### 2.1 设计原则

```
┌─────────────────────────────────────────────────────────┐
│  Plugin: curve-memory                                        │
│                                                           │
│  对外提供：                                                 │
│  ┌────────────┐  ┌──────────┐  ┌──────────┐  ┌───────┐  │
│  │ CLI 命令    │  │ Provider  │  │ Cron     │  │ Tool  │  │
│  │ curve-     │  │ (Memory  │  │ (t++,    │  │ (语义 │  │
│  │ memory-cli  │  │ Provider)│  │ index)   │  │ 搜索) │  │
│  └────────────┘  └──────────┘  └──────────┘  └───────┘  │
│                                                           │
│  持久化层：                                                 │
│  ~/.hermes/memories/ (目录结构 + ACTIVITY.yaml + 文件)      │
└─────────────────────────────────────────────────────────┘
```

**核心原则：持久化格式与框架解耦。**

Plugin 的所有数据（`ACTIVITY.yaml`、`active/*.md`、`.embedding_index/*.jsonl`、`.fts5/curve_memory_fts5.db`）都是标准格式（YAML/Markdown/JSONL/SQLite），不依赖 Hermes 内部的任何对象格式。这意味着：

- 同一 Hermes 实例的不同 profile 可以共享遗忘曲线系统
- 其他框架可以通过读文件直接接入
- 未来迁移时数据不锁定

### 2.2 Plugin 注册的组件

| 组件 | 类/文件 | 说明 |
|------|---------|------|
| **MemoryProvider** | `CurveMemoryProvider` | 实现 `prefetch()` 做三路检索 + TIER 控制注入，`sync_turn()` 更新 t 值 |
| **CLI** | `curve-memory-cli` 子命令 | `hermes curve-memory-cli search/read/touch/status` |
| **Cron** | `curve-memory-forgetting.py` | 每日 t++、归档、检查成熟度 |
| **Cron** | `curve-memory-indexer.py` | 检测 active/ 变动 → re-chunk → re-embed → 更新 FTS5 |
| **Tool** | `curve_memory_semantic_search` | agent 可调用的三路检索工具 |
| **Skill** | `curve-memory` | agent 协议文档（R(t) 公式、TIER 映射、行为规则） |

### 2.3 CLI 命令设计

```bash
hermes curve-memory-cli init                     # 初始化目录结构 + ACTIVITY.yaml
hermes curve-memory-cli search "query"           # 三路检索 → 返回 top-5 + R(t) + 片段
hermes curve-memory-cli read <topic>             # 按 TIER 读文件内容
hermes curve-memory-cli touch <topic>            # 置 t=0, access_count++
hermes curve-memory-cli status                  # 活跃记忆概览 + TIER 分布
hermes curve-memory-cli daily-tick              # 手动触发每日衰减
hermes curve-memory-cli index                   # 手动触发 embedding + FTS5 索引更新
hermes curve-memory-cli forget <topic>          # 手动归档
hermes curve-memory-cli mature <topic>          # 手动标记成熟
```

`search` 命令内部执行完整的三路检索——对 agent 和用户都透明。

### 2.4 跨框架复用模型

```
复用到同一 Hermes 的不同 profile
  → hermes plugins install curve-memory
  → 所有 profile 共享 ~/.hermes/memories/ 目录

复用到另一台机器上的 Hermes
  → hermes plugins install（从 registry 或本地源）
  → 数据在 ~/.hermes/memories/，可 rsync 迁移

复用到 Claude Code / Codex / Cursor
  → skill 文档中写：
      运行 `curve-memory-cli search "query"` 检索记忆
      读取 `~/.hermes/memories/active/<topic>.md`
  → 任何能执行 shell 命令的 agent 都能用

复用到非 agent 系统（脚本/工程）
  → 直接读写 ~/.hermes/memories/ 下的纯文本文件
  → 或调用 `curve-memory-cli search "query" --format json`
  → 不依赖任何 Hermes 代码
```

### 2.5 Provider 行为设计

`CurveMemoryProvider` 实现 `MemoryProvider` 接口：

```python
class CurveMemoryProvider(MemoryProvider):
    name = "curve-memory"

    def is_available(self) -> bool:
        """检查 ACTIVITY.yaml 和 ~/.hermes/memories/ 目录是否存在"""
        return (get_hermes_home() / "memories" / "ACTIVITY.yaml").exists()

    def initialize(self, session_id: str, **kwargs):
        """初始化——加载 ACTIVITY.yaml，准备 embedding provider，连接 FTS5"""
        config = kwargs.get("config", {})
        emb_cfg = config.get("embedding", {})
        provider_name = emb_cfg.get("provider", "sentence-transformers")
        model_name = emb_cfg.get("model", "all-MiniLM-L6-v2")

        if provider_name == "ollama":
            self.embedder = OllamaProvider(
                model=model_name,
                base_url=emb_cfg.get("base_url", "http://localhost:11434")
            )
        else:
            try:
                self.embedder = SentenceTransformersProvider(model_name)
            except ImportError:
                self.embedder = None  # 降级为 BM25 + R(t)

    def system_prompt_block(self) -> str:
        """注入当前活跃记忆概览（仅 TIER_3 以上告知 agent 有什么可用）"""
        lines = ["## 遗忘曲线记忆系统"]
        for topic in self.get_active_topics():
            t, r = self.get_r(topic)
            if r >= 0.503:
                lines.append(f"- {topic} (R={r:.2f})")
        return "\n".join(lines) if len(lines) > 1 else ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """三路混合检索 → 按 TIER 决定注入内容"""
        if not query.strip():
            return ""
        # 并行三路
        bm25_scores = self.fts5_search(query)          # FTS5 BM25
        cosine_scores = self.semantic_search(query)     # embedding
        r_values = self.get_r_values(topics)            # ACTIVITY.yaml
        # 归一化 + 加权融合
        final = self.hybrid_fuse(bm25_scores, cosine_scores, r_values)
        # TIER 缩减
        blocks = []
        for topic, text, r in final[:3]:
            tier = r_to_tier(r)
            snippet = self.truncate_by_tier(text, tier)
            blocks.append(f"### {topic} ({tier})\n{snippet}")
        return "## 召回记忆\n" + "\n\n".join(blocks)

    def sync_turn(self, user: str, asst: str):
        """对话结束后：更新 touch 过的记忆的 t=0, access_count++"""

    def get_tool_schemas(self) -> list:
        """注册 curve_memory_semantic_search 工具"""
        return [{
            "name": "curve_memory_semantic_search",
            "description": "三路混合检索记忆系统（BM25 + 语义 + 遗忘曲线）",
            "parameters": {...}
        }]
```

### 2.6 与 ADR-003 的关系

```
ADR-003: 遗忘曲线（R(t) + TIER + 归档）
  └─ 框架无关的数学和协议
     ↓
ADR-004: 三路混合检索 + Plugin 封装
  ├─ 三路检索（BM25 + embedding + R(t)）：在 ADR-003 之上的增强层
  └─ Plugin：将 ADR-003 + 三路检索打包为可复用组件
```

ADR-003 的数学公式、TIER 映射表、归档逻辑**全部不变**。ADR-004 只增加：
- 检索层的三路加权融合
- 封装层的 plugin 结构
- embedding 索引的管理规范
- FTS5 索引的维护规范

---

## 影响分析

### 正面影响

| 方面 | 描述 |
|------|------|
| 检索精度 | BM25 精确匹配 + embedding 语义匹配 + R(t) 新鲜度加权，三路互补 |
| 复用性 | Plugin 安装即用，CLI 可被任何框架调用 |
| 降级安全 | 五级降级链，每一级不影响 agent 调用接口 |
| 数据中立 | YAML/Markdown/JSONL/SQLite 标准格式，不锁死 |
| 迁移路径 | 从 ADR-003 过渡无需改变任何文件格式 |

### 负面风险

| 风险 | 缓解措施 |
|------|---------|
| embedding 模型安装依赖 | 首次 `curve-memory-cli index` 时自动检测，未安装则提示 `pip install sentence-transformers` 并降级为 BM25 + R(t) |
| FTS5 索引需要 SQLite | SQLite 是 Python 标准库，零额外依赖 |
| 索引量增长 | 每个 topic 3-5 个 chunk，500 条记忆的 embedding 索引约 4 MB，FTS5 约 < 1 MB |
| Plugin API 变动 | 保持 CLI 独立——即使 Hermes plugin 接口升级，`curve-memory-cli` 命令仍然可用 |
| 初始冷启动 | 首次 `curve-memory-cli index` 扫描全部 active/*.md，一次性构建两路索引 |

---

## 未采纳方案

### 未采纳：仅 embedding（无 BM25）

**理由：** embedding 对罕见 token（类名、路径、缩写）的 recall 不稳定。"ACTIVITY.yaml"作为 query 可能被 embed 到一个无关语义区域。BM25 在精确术语上永远可靠。两者互补而非替代。

### 未采纳：仅 BM25（无 embedding）

**理由：** 纯关键词方案就是 ADR-003 的现状。用户说"borrow checker"但索引是"rust-lifetimes"时无法命中，这恰恰是本 ADR 要解决的问题。

### 未采纳：渐进式（先方案二，再升级方案三）

**理由：** 方案三不是方案二的上层扩展——FTS5 索引、归一化融合层、权重配置都是在方案二中不存在的新组件。先做方案二再做方案三相当于重做一半。三路的 SQLite FTS5 + embedding + R(t) 一次性实现约 800 行，增量做两遍反而更多。

### 未采纳：嵌入到 memory tool 内部

**理由：** memory tool 是 Hermes 内置工具，直接修改源码会增加维护成本和升级冲突。Plugin 作为独立模块更安全。

### 未采纳：替代 session_search（FTS5）

**理由：** 遗忘曲线系统管的是"知道什么"（主动记忆），session_search 管的是"在哪里说过"（会话历史）。两个不同的关注点。方案三中的 FTS5 索引针对 `active/*.md` 建独立表，不和 session_search 共用。

### 未采纳：外部向量数据库（Pinecone / Chroma / Qdrant）

**理由：** 100-300 条记忆的规模下，本地 JSONL 文件 + 线性 cosine 扫描足够快（384 维向量，1000 个 chunk < 20ms）。引入独立数据库是过度工程。

---

## 实现计划

### Phase 1: curve-memory-indexer.py（~2 小时）

- [ ] 按 `##` 分 chunk + 生成 `.embedding_index/<topic>.jsonl`
- [ ] 对 active/*.md 建立 SQLite FTS5 表（`.fts5/curve_memory_fts5.db`）
- [ ] 增量更新：只 re-index mtime 变更的文件
- [ ] 归档时清理对应的 `.embedding_index/` + FTS5 条目
- [ ] `sentence-transformers` 懒加载检测——未安装时提示用户：

      ```bash
      pip install sentence-transformers
      # 或：hermes setup 中提供系统依赖检查
      ```

      提示后自动降级为 BM25 + R(t)，不阻断系统首次运行。

### Phase 2: curve-memory-cli CLI（~2 小时）

- [ ] 独立 CLI 入口，不依赖 Hermes 内部 API
- [ ] `search`：三路检索 → 归一化 → 加权融合 → 返回 topic + R(t) + snippet
- [ ] `read`：按 TIER 读文件
- [ ] `touch`：更新 ACTIVITY.yaml
- [ ] `status`：活跃记忆概览 + TIER 分布 + 索引大小
- [ ] 输出格式：JSON（machine-readable）和 text（human-readable）

### Phase 3: Plugin 封装（~2 小时）

- [ ] 创建 `plugins/memory/curve-memory/` 目录结构
- [ ] 实现 `CurveMemoryProvider`（prefetch 三路检索 + sync_turn）
- [ ] 注册 cron（curve-memory-forgetting.py + curve-memory-indexer.py）
- [ ] 注册 tool `curve_memory_semantic_search`
- [ ] 编写配套 skill（agent 行为协议）
- [ ] 安装/卸载脚本

### Phase 4: 集成测试（~1 小时）

- [ ] 验证 provider 的 prefetch 三路检索 + TIER 控制注入
- [ ] 验证 cron 的每日衰减 + 归档 + 索引更新
- [ ] 验证五级降级链每级行为正确
- [ ] 验证无 sentence-transformers 时的降级
- [ ] 验证独立 CLI 在无 Hermes 环境下的可用性
- [ ] 验证跨 profile 共享目录

---

## 附录 A：全链路延时分解

```
用户发送消息
  │
  0ms    ├── FTS5 BM25 检索     ← SQLite 全文索引，< 5ms
  5ms    ├── embed(query)       ← CPU 30ms
 35ms    ├── cosine_sim 扫描     ← 1000 chunk × 384 维 < 10ms
 45ms    ├── 三路归一化 + 融合    ← 纯数学，< 1ms
 46ms    ├── 查 ACTIVITY.yaml   ← 文件 I/O 或缓存，< 1ms
 47ms    ├── TIER 判定 + read  ← 文件 I/O，< 1ms
 48ms    └── 注入 system prompt
         └── LLM 推理（数秒，非本系统负责）
```

检索总耗时约 **50ms**。

## 附录 B：跨框架复用速查

| 目标框架 | 接入方式 | 需要什么 |
|----------|---------|---------|
| Hermes (同实例) | `hermes plugins install curve-memory` | Plugin |
| Hermes (其他实例) | 同上 + 拷贝 `~/.hermes/memories/` | Plugin + 数据 |
| Claude Code | Skill 文档 + `!curve-memory-cli search "query"` | CLI + Skill |
| Codex CLI | `run("curve-memory-cli search 'query'")` | CLI + Skill |
| Cursor | `.mdc` 规则 + 终端命令 | CLI + Skill |
| 任何 shell 脚本 | `curve-memory-cli search "query" --format json` | CLI only |
| Python 脚本 | `subprocess.run(["curve-memory-cli", "search", query])` | CLI only |

## 附录 C：Plugin 目录结构

```
~/.hermes/hermes-agent/plugins/memory/curve-memory/
├── __init__.py                ← plugin 入口，注册所有组件
├── provider.py                ← CurveMemoryProvider
├── cli.py                    ← curve-memory-cli CLI 入口
├── search.py                 ← 三路检索核心（FTS5 + cosine + R(t) 融合）
├── indexer.py                ← embedding + FTS5 索引管理
├── activity.py               ← ACTIVITY.yaml 读写
├── tier.py                   ← R(t) 计算 + TIER 映射
├── chunker.py                ← Markdown chunk 分割
├── scripts/
│   ├── curve-memory-forgetting.py  ← cron：每日衰减 + 归档
│   └── curve-memory-indexer.py     ← cron：embedding + FTS5 索引更新
└── skill/
    └── SKILL.md              ← agent 协议文档
```
