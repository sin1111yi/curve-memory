---
name: curve-memory
description: 遗忘曲线记忆系统 — R(t) 遗忘曲线 + 三路混合检索 + 双层归档
version: 1.0.0
---

# 遗忘曲线记忆系统

## 核心公式

```
R(t) = 0.462 + 0.538 · exp(-t / 2.71)

R(t) ∈ [0.462, 1.0]  ← 基线 46.2%，永不归零
t = 距离上次访问的天数
```

ralqlator 验证: `ralqlator "0.462 + 0.538 * pow(C_E, -t / 2.71)"`

## TIER 映射

| TIER | R(t) | t | 详细度 |
|------|------|---|--------|
| TIER_5 🔥 | R ≥ 0.800 | t ≤ 1 | 全量加载 |
| TIER_4 📗 | R ≥ 0.640 | t ≤ 3 | 核心 + 细节 |
| TIER_3 📙 | R ≥ 0.503 | t ≤ 7 | 摘要 |
| TIER_2 📕 | R ≥ 0.465 | t ≤ 14 | 极简一行 |
| TIER_1 📦 | R > 0.462 | 14-30 | 归档待命 |
| ARCHIVE 🗄️ | R ≈ 0.462 | ≥ 30 | 已归档 |

## 每会话协议

```
1. 解析 idx:topic [t=N] 索引
2. 根据对话加载相关 active/<topic>.md
3. 更新 ACTIVITY.yaml:
   - 被读的记忆: t=0, access_count+=1
   - 其他: 不动（cron 全量+1）
4. 检查 t ≥ 30 → 通知用户归档
5. 如果用到 archive/ 内容 → 重新激活
```

## 温度回弹

记忆重新使用时，agent 根据当前上下文补全内容至 TIER_5。

## CLI 命令

```bash
cd ~/.hermes/scripts
python3 curve-memory-cli.py search "query"          # 三路检索
python3 curve-memory-cli.py status                   # 状态概览
python3 curve-memory-cli.py touch <topic>            # 置 t=0
python3 curve-memory-cli.py daily-tick               # 手动衰减
```

## 文件结构

| 路径 | 说明 |
|------|------|
| `~/.hermes/memories/ACTIVITY.yaml` | t, access_count, mature |
| `~/.hermes/memories/active/` | 活跃记忆 |
| `~/.hermes/memories/archive/forgotten/` | 遗忘归档 |
| `~/.hermes/memories/archive/mature/` | 成熟归档快照 |
| `~/.hermes/knowledge/` | 固化知识 |
| `~/.hermes/memories/.embedding_index/` | 语义索引 |
| `~/.hermes/memories/.fts5/` | FTS5 索引 |

## 降级链

0: BM25 + Embedding + R(t) → 3: R(t) only → 4: pure idx match
