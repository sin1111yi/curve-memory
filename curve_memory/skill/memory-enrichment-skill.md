---
name: memory-enrichment
description: 记忆丰富协议 — TIER驱动的磁盘降级 + 对话上下文提取与追加
version: 1.0.0
depends_on: curve-memory
---

# 记忆丰富系统 (Memory Enrichment)

## 核心概念

记忆丰富系统在遗忘曲线的基础上增加两层能力：

1. **主动降级**：当记忆的 R(t) 下降穿越 TIER 边界时，物理重写文件为更短的形式
2. **内容丰富**：当对话中提到某个记忆主题时，提取新信息并追加到文件中

## TIER 内容大小限制

| TIER | 最大内容 | 格式 |
|------|---------|------|
| TIER_5 | 4000 chars | 完整内容 |
| TIER_4 | 2000 chars | 核心 + 细节 |
| TIER_3 | 800 chars | 摘要 (5-10行) |
| TIER_2 | 300 chars | 关键点一行 |
| TIER_1 | 100 chars | 主题 + 一句话 |

## 何时丰富

### 自动场景（系统自动，无需 agent 操作）

| 事件 | 行为 |
|------|------|
| sync_turn() 检测到主题被提及 | 自动调用 _touch_memory() 重置 t=0 |
| TIER 向下穿越边界 | 自动 degrade_memory() 重写文件 |
| initialize() 启动 | 自动 degradation_sweep() 检查所有记忆 |

### Agent 主动场景（需要调用工具）

| 事件 | 动作 | 工具 |
|------|------|------|
| 对话揭示了已知主题的新信息 | 调用 curve_memory_enrich | `topic`, `content` |
| 需要强制重置所有记忆到当前 TIER | 调用 curve_memory_degrade_now | (无参数) |

## 如何格式化丰富内容

调用 `curve_memory_enrich` 时，`content` 参数应该：

### 格式要求

- 简洁、事实性、Markdown 格式
- 每个新事实一行（方便去重检测）
- 如果是对话中得出的结论，标注来源
- 不要重复文件中已有的内容（系统会自动去重）

### 示例

好的 content:

```
- 用户喜欢使用 Neovim 作为编辑器
- 偏好东京夜间主题 (Tokyo Night)
- 通常使用腾龙 28-75mm f/2.8 镜头拍摄人像
```

不好的 content（太啰嗦、或重复已有内容）:

```
During our conversation, the user mentioned that they really like using Neovim as their code editor... (冗长)
```

### 结构提示

当对话中识别到 `[topic]` 被多次提到且有新信息时：

1. 用 `curve_memory_search` 搜索 topic 获取当前内容
2. 对比对话记录，提取文件中没有的新事实
3. 调用 `curve_memory_enrich` 追加新内容

## 降级策略说明

### TIER_5 → TIER_4
当记忆超过 1 天未被访问（或 R(t) < 0.800），降级为 TIER_4：
- 保留前 2000 字符
- 裁剪末尾最旧的内容

### TIER_4 → TIER_3
当记忆超过 3 天未被访问（或 R(t) < 0.640），降级为 TIER_3：
- 保留前 5 个有意义行
- 格式转为摘要

### TIER_3 → TIER_2
当记忆超过 7 天未被访问（或 R(t) < 0.503），降级为 TIER_2：
- 仅保留第一行关键点（标题 + 一句话描述）

### TIER_2 → TIER_1
当记忆超过 14 天（或 R(t) < 0.465），降级为 TIER_1：
- 仅保留 `[topic]: key point` 形式的一行

## 丰富内容生命周期

写入 enrichment 追加的内容本身也会经历降级：

- 新追加的内容初始为 TIER_5 级别
- 随着时间推移，追加的内容会随文件一起被降级
- 降级时优先保留**最早**的核心事实，裁剪**最新**的细节
- 这意味着 agent 需要定期重新丰富关键记忆

## 协议：搜索 → 比较 → 提取 → 丰富

```
1. 使用 curve_memory_search 搜索相关记忆
2. 阅读搜索到的 active/<topic>.md 内容
3. 与对话上下文对比，提取文件中不存在的新事实
4. 调用 curve_memory_enrich(topic=<topic>, content=<新事实>)
```
