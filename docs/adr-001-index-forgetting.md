# ADR-001: Memory Index + Forgetting Mechanism

**状态：** 已接受
**阶段：** 🏗️ 建设期
**时间：** 2026-05-21

## 上下文

Hermes memory 工具将记忆以条目的形式通过 § 分隔注入每个会话的 system prompt。当前设计有三个痛点：

1. **字符上限** — MEMORY.md 的 `memory_char_limit` 已从 2200 提升到 8800，但仍然是硬上限
2. **内容过载** — 所有条目每次都被注入，即使当前对话完全不相关
3. **无遗忘机制** — 记忆只增不减，最终必然撑爆上限

此外，分析 Hermes memory 工具的实现后，发现了另一个关键约束：

**冻结快照模式（Frozen Snapshot）：** `MemoryStore.load_from_disk()` 在会话启动时读取 MEMORY.md/USER.md，将其渲染为 system prompt 的一部分。工具调用可以写入磁盘（持久化），但 **system prompt 在会话期间不会更新**。这意味着：
- 所有活跃度计数更新只能在**下次会话**生效
- 中会话的 `memory("replace")` 不会影响当前会话的索引内容
- 活跃度持久化方案必须兼容这一模式

## 决策

### 1. 三层记忆体系

```
memory tool (system prompt)  →  索引条目 (~600 chars, 12条)
  ↕ 按需加载 (read_file)
~/.hermes/memories/active/   →  记忆正文 (每条 50-500 chars)
  ↕ 超过阈值自动归档
~/.hermes/memories/archive/  →  冷存储 (可重新激活)
```

### 2. 索引格式

```
idx:<topic> [act=<N>] → active/<topic>.md
```

示例：
```
idx:skill-backup [act=0] → active/skill-backup.md
idx:workflow [act=3] → active/workflow.md
idx:rust-learning [act=12] → active/rust-learning.md
```

**选择理由：**
- `[act=N]` 比原方案的 `[N]` 更明确可读 — 括号不跟路径混淆，`act=` 前缀消除歧义
- 纯文本格式，agent 可用肉眼或简单文本搜索解析
- 每条 ~50-60 chars，12 条约 600-720 chars，远低于 8800 上限

### 3. 活跃度持久化：方案 B — 独立 ACTIVITY.yaml

```
~/.hermes/memories/ACTIVITY.yaml
```

```yaml
# Memory Activity Counter
# Each memory gets +1 per day (cron) if not accessed.
# Current session resets to 0.
# Threshold: 30 → moves to archive/
skill-backup: 0
coder-rules: 0
workflow: 0
project-public: 0
thetagp-project: 0
architect-vs-coder: 0
snowlyn-image: 0
workflow-no-exception: 0
searxng: 0
bus-naming: 0
generator-pattern: 0
rust-learning: 0
```

**选择理由：**

| 标准 | 方案 A (内联) | 方案 B (独立文件) | 方案 C (frontmatter) |
|------|--------------|-----------------|---------------------|
| 更新复杂度 | 需 `memory("replace")` + 文本匹配 | 只需 `write_file` 写 YAML | 需解析 frontmatter 后重写整个文件 |
| 冻结快照兼容性 | ❌ 内联计数在冻结 prompt 中不会变化 | ✅ 计数在独立文件中，不会触发 prompt 变更 | ❌ 计数混在数据文件中，每次更新都需重写全文 |
| 原子性 | ❌ memory 替换失败会破坏索引 | ✅ YAML 写入可原子化 | ⚠️ 可原子化但需重写整个文件 |
| 可维护性 | ⚠️ 需要手动维护计数串 | ✅ 独立的 YAML，脚本友好 | ⚠️ 每个文件都带 frontmatter |
| 脚本/CRON 支持 | ❌ 需要通过 memory tool | ✅ 直接 YAML 解析 | ⚠️ 需逐个文件解析 |
| 可读性 | ✅ 索引本身就是完整的 | ✅ 所有计数集中一处 | ❌ 计数分散在各文件中 |

**结论：方案 B 胜出。** 独立 YAML 文件解耦了活跃度管理和记忆内容管理，脚本友好，不违反冻结快照约束，并且新增/删除记忆时只需在一处修改。

索引条目中的 `[act=N]` 是 ACTIVITY.yaml 的**只读快照**——在会话启动时从 YAML 读取并写入索引。会话期间 agent 直接更新 YAML（通过 `write_file`），下次 session 启动时 `memory` 工具重新从 YAML 读入索引条目。

### 4. 活跃度规则

```
当前模型（session-based + time-based）：
- 每次 agent 主动 read_file 某记忆 → 该记忆 act=0，其他不变（cron 全量+1）
- 每日 cron 触发全量衰减 → 所有记忆 act += 1
- 任何记忆 act > THRESHOLD(30) → 触发遗忘归档

修正：原方案纯 session-based 有缺陷
  问题：如果一天 10 个 session，30 天阈值 → 3 天就能遗忘
  解决：session-based 增量仅限主动 read_file 场景，cron 的日衰减是主要的遗忘驱动力
  
最终模型：
| 事件 | act 变化 |
|------|---------|
| agent 主动 read_file 该记忆 | 该记忆 = 0，其他不变（cron 全量+1） |
| 每日 cron 衰减 | 所有记忆 += 1 |
| act > 30 | 触发遗忘归档 |
```

阈值 30 的合理性分析：
- 主要驱动力是 cron 日衰减 → 30 天未使用即归档
- session-based 增量是辅助的（只有 agent 主动读文件时才触发）
- 30 天是一个合理的遗忘窗口：如果一个记忆 30 天没用过，且之后用户突然需要，仍然可以从 archive/ 重新激活
- 推荐起始值 30，可根据使用情况调整（通过 `hermes config set` 或修改 YAML 中的 THRESHOLD 值）

### 5. 遗忘触发机制

**两层触发：**

```
层级 1：会话内 (agent 主动)
  └─ 每次 agent 完成记忆读取 + 计数更新后
  └─ 检查 act > 30 的记忆
  └─ 执行归档：mv active/ → archive/ + memory remove 索引

层级 2：定时 cron
  └─ 每日 cron job：遍历所有记忆 +1
  └─ 检查 act > 30 的记忆
  └─ 执行归档
  └─ 是层级 1 的兜底
```

**为什么需要 cron：**
- 如果会话崩溃或 agent 未正确执行遗忘检查
- 确保即使在无会话的天数，记忆也会自然衰减
- cron 是独立于 agent 行为的守护机制

**为什么需要 session-end 检查：**
- 实时性：如果某个记忆在会话中被大量衰减，当场归档比等到下次 cron 更好
- 减少 cron 的负担

**cron job 规格：**
- 表达式：`0 3 * * *`（每天凌晨 3 点）
- 名称：`snowlyn-memory-decay`
- 动作：读取 ACTIVITY.yaml → 所有 act += 1 → 标记待归档 → 执行归档 → 写回 YAML

### 6. 按需加载策略

Snowlyn 在每次对话中执行以下协议：

```
┌─ 1. 读取 system prompt 中的 memory 索引
├─ 2. 解析 idx:xxx [act=N] → active/xxx.md
├─ 3. 评估当前对话上下文，判断哪些 topic 相关：
│     ① 用户消息中的关键词匹配 topic 名称
│     ② 会话历史中近期提到的话题
│     ③ 默认加载：workflow (代码流程总是相关)
│
├─ 4. 对每个相关 topic 调用 read_file("active/topic.md")
├─ 5. 读取后，更新 ACTIVITY.yaml：
│     ① 被读取的 topic: act=0
│     ② 所有其他 topic: 不做操作（crond 全量+1）
│     ③ 写回文件
│
├─ 6. 检查是否有 act > 30 的记忆
└─ 7. 如果发现，执行遗忘归档
```

已读文件的去重约束：同一个文件在同一个会话中只能 read_file 一次（Hermes 的 block 机制）。这符合设计——不需要重复加载。

### 7. 归档后重新激活

```
当 agent 遇到 archive/<topic>.md 相关的内容时：
  1. 检查 ~/.hermes/memories/archive/<topic>.md 是否存在
  2. 如果存在，读取内容
  3. 执行反向归档：
     mv archive/<topic>.md active/<topic>.md
     memory add "idx:<topic> [act=0] → active/<topic>.md"
     从 ACTIVITY.yaml 设置 act=0
  4. 在回复中说明：已重新激活记忆「topic」
```

### 8. 与 Hermes 原生 memory 工具的共存策略

```
┌─────────────────────────────────────────────────┐
│ MEMORY.md (memory tool 注入到 system prompt)      │
│                                                   │
│ --- 索引区域 (agent 管理的索引条目) ---             │
│ idx:workflow [act=5] → active/workflow.md         │
│ idx:searxng [act=0] → active/searxng.md           │
│ ...                                                │
│                                                   │
│ --- 小型事实区域 (共存，暂不索引化的直接条目) ---    │
│ 雪精灵形象已经画好并存入 ~/.hermes/snowlyn_work.png  │
│ 和 snowlyn_home.png...                              │
└─────────────────────────────────────────────────┘

┌──────────────────────────────┐
│ USER.md (不受影响，保持不变)   │
│                               │
│ 主人叫法、时间偏好、隐私规则...  │
└──────────────────────────────┘
```

**共存规则：**
1. MEMORY.md 中同时包含索引条目（`idx:` 前缀）和传统小条目（无前缀）
2. agent 通过 `idx:` 前缀区分索引条目和非索引条目
3. 传统小条目（< 150 chars）继续保持直接注入——它们小到不值得文件开销
4. USER.md 暂不引入索引机制——用户资料变化频率低，且 `user_char_limit = 1375` 足够
5. 非索引条目的活跃度不做追踪——它们始终在 prompt 中

### 9. 用户 profile 是否适用

**当前设计：不适用。**

理由：
- 用户资料特征稳定（时区、语言偏好、隐私规则），不需要遗忘
- `user_char_limit = 1375` 当前使用约 600 chars，空间充足
- 用户资料条目数量少（当前 5 条），注入开销可忽略
- 如果未来用户资料膨胀到接近上限，再考虑引入索引

**未来扩展：** 可以为 USER.md 引入同样的索引机制，但优先级低。

### 10. 目录结构

```
~/.hermes/memories/
├── ACTIVITY.yaml          ← 活跃度计数器
├── MEMORY.md              ← 索引 + 小型事实 (memory tool 管理)
├── USER.md                ← 用户资料 (不变)
├── active/                ← 活跃记忆正文
│   ├── skill-backup.md
│   ├── coder-rules.md
│   ├── workflow.md
│   ├── project-public.md
│   ├── thetagp-project.md
│   ├── architect-vs-coder.md
│   ├── snowlyn-image.md
│   ├── workflow-no-exception.md
│   ├── searxng.md
│   ├── bus-naming.md
│   ├── generator-pattern.md
│   └── rust-learning.md
└── archive/               ← 遗忘归档 (冷存储)
    └── (初始为空)
```

**命名评价：** `active/` 和 `archive/` 是最清晰的选择。备选 `hot/`/`cold/` 不够直观。保持当前命名。

文件命名约定：`<topic>.md`，全小写、连字符连接。文件名必须与索引中的路径一致。

## 替代方案

### 未采纳：插件方案（mem0, supermemory 等）
- Hermes 插件目录中有多个外部记忆插件
- 但引入了外部依赖（数据库、向量索引），对 Snowlyn 当前需求过重
- 文件系统方案零依赖，脚本可控

### 未采纳：SQLite 存储活跃度
- 比 YAML 更正式，但需要 SQL 查询
- YAML 可以直接用 `write_file` 读写，agent 友好

### 未采纳：纯 session-based 活跃度（原始方案）
- 缺陷：session 频次不固定，导致遗忘速度不可预测
- 修正后：time-based（cron）为主，session-based 为辅

## 影响

### 正面
- 记忆容量从 8800 chars 扩展到无上限（文件系统）
- 按需加载减少 prompt 噪音
- 遗忘机制防止记忆膨胀
- 归档保留了「后悔药」——可以从 archive 重新激活

### 负面
- 增加协议复杂度：agent 需要遵循索引→加载→计数→遗忘的协议
- 多了一个移动部件：ACTIVITY.yaml 需要维护
- 已读文件去重约束（read_file 阻塞）可能阻止某些场景下的重新加载——但这是预期的

### 迁移
- 迁移期间 MEMORY.md 将包含新旧格式混合的内容
- 迁移完成后，原本 3210 chars 的 MEMORY.md 将缩减到约 600 chars（索引）
- 从 MEMORY.md 中移除的 2600 chars 内容转移到 active/*.md

## 对 Snowlyn 的指导

修改 Snowlyn 的 SOUL.md 或 system prompt 配置，添加以下记忆协议：

```
## 记忆系统使用协议

系统使用三层记忆体系：
1. MEMORY.md 存索引：idx:<topic> [act=<N>] → active/<topic>.md
2. active/*.md 存完整内容（read_file 读取）
3. ACTIVITY.yaml 管理活跃度

每次对话时：
1. 解析 MEMORY.md 中的 idx: 索引
2. 根据上下文判断需要加载哪些 topic
3. 调用 read_file("active/<topic>.md")
4. 更新 ACTIVITY.yaml：被读的 = 0，其他的不动（cron 全量+1）
5. 检查 act > 30 的记忆 → mv 到 archive/ + 删除索引
6. 如果遇到 archive/ 相关的 topic → 重新激活
```

## 对 Reviewer 的要求

```
### 架构完整性
- [ ] 索引条目格式一致（idx:xxx [act=N] → active/xxx.md）
- [ ] ACTIVITY.yaml 格式正确，无重复条目
- [ ] 迁移后的 active/*.md 内容完整，未丢失信息
- [ ] 归档后的索引条目已从 MEMORY.md 中移除
```

---

## 实现计划

### Phase 1: 迁移现有记忆（立即执行）

**步骤 1.1:** 将当前 MEMORY.md 中的 12 条内容拆分为独立的 active/*.md 文件
**步骤 1.2:** 创建 ACTIVITY.yaml，所有记忆初始活动度 = 0
**步骤 1.3:** 重写 MEMORY.md，包含索引条目 + 保留小型事实条目
**步骤 1.4:** 验证：检查 MEMORY.md 字符数（应在 1500 以内）

### Phase 2: Agent 协议集成（立即执行）

**步骤 2.1:** 在 Snowlyn 的 SOUL.md 或系统配置中添加记忆系统协议
**步骤 2.2:** 创建一个示例会话演示索引读取→加载→更新流程

### Phase 3: cron job 遗忘守护（一周内）

**步骤 3.1:** 创建 `snowlyn-memory-decay` cron job（每天凌晨 3 点）
**步骤 3.2:** cron job 内容：读取 ACTIVITY.yaml → 所有 +1 → 归档超阈值记忆 → 更新 MEMORY.md → 写回 YAML
**步骤 3.3:** 验证：检查 cron 日志确认执行

### Phase 4: 优化（后续）

**步骤 4.1:** 自动重新激活逻辑
**步骤 4.2:** 遗忘事件日志（记录到 sessions/ 或 cron output/）
**步骤 4.3:** 每月归档回顾报告
**步骤 4.4:** 如果用户 profile 膨胀，引入同样的索引机制
