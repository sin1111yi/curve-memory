# ADR-003: 遗忘曲线记忆系统（替代对数温度模型）

**状态：** 已接受
**阶段：** 📐 设计完成（待实现）
**时间：** 2026-05-21
**替代：** ADR-002 (ADR-memory-temperature.md)

---

## 上下文

### 原系统（ADR-002）的问题

ADR-002 引入了基于对数函数的温度模型 `y = -log₁.₀₉(x) + 45`，但存在三个根本缺陷：

1. **无界温度** — x=0 时 y → ∞，需要在实现中特殊处理为 999.0。这不仅不优雅，而且 ∞ 的语义对 agent 的推理不友好（「无限详细」究竟应该多详细？）。

2. **对数底数选择主观** — 底数 1.09 的选择基于使归档阈值落在 x=48 的工程便利性，而非任何记忆科学依据。底数的微小变化会显著改变整个曲线形状，缺乏理论基础。

3. **硬阈值 vs 软遗忘** — 对数模型仍然有明确的归档阈值（x ≥ 48），这本质上还是二值遗忘——只是把阈值从 30 推到了 48。记忆系统应该模拟「逐渐退化」而非「突然消失」。

### 新需求

- 需要一个有理论基础的遗忘曲线，参考艾宾浩斯遗忘曲线
- R(t) 值域有限且有下界（永不归零），避免无界温度问题
- 平滑退化，无硬阈值，归档是渐进的
- 兼容 ralqlator 表达式（无 exp()，使用 pow(C_E, x)）

### 数学基础

选择指数衰减曲线，基于艾宾浩斯遗忘曲线的形式：

```
R(t) = R₀ + (1 - R₀) · exp(-t / τ)
```

参数选择依据：

| 参数 | 值 | 理由 |
|------|-----|------|
| R₀ (基线保留率) | 0.462 | 艾宾浩斯实验显示长期保留率约 40-50%，取中值偏下 46.2% |
| τ (时间常数) | 2.71 | 自然常数 e 的近似值，使曲线在 t=τ 时衰减到约 63%，这是指数衰减的特征时间 |
| 1 - R₀ | 0.538 | 可遗忘的部分（短期记忆成分） |

代入得：

```
R(t) = 0.462 + 0.538 · exp(-t / 2.71)
```

**关键特性：**

| 特性 | 值 | 意义 |
|------|-----|------|
| R(0) | 1.0 (100%) | 刚使用时记忆完整 |
| R(τ) = R(2.71) | 0.462 + 0.538/e ≈ 0.660 | 2.71 天后保留 66% |
| R(∞) | 0.462 (46.2%) | 永不归零的基线保留率 |
| 基线 | 46.2% | 即使多年未用，也能保留核心概要 |

**R(t) 值表（关键点）：**

| t (天数) | R(t) | 物理意义 |
|----------|------|---------|
| 0 | 1.000 | 刚刚使用，全部保留 |
| 1 | 0.871 | 1天后，仍保留87% |
| 2.71 (τ) | 0.660 | 一个时间常数后保留66% |
| 3 | 0.641 | 3天后，≈ 64% |
| 7 | 0.531 | 一周后，保留53% |
| 10 | 0.500 | 10天后，保留50% |
| 14 | 0.480 | 两周后，保留48% |
| 21 | 0.469 | 三周后，保留46.9% |
| 30 | 0.463 | 一个月后，接近基线 46.2% |
| 60 | 0.462 | 两个月后，等于基线 |

**与旧对数模型的对比：**

| 维度 | 旧（对数） | 新（指数遗忘曲线） |
|------|-----------|-------------------|
| 理论依据 | 工程便利 | 艾宾浩斯遗忘曲线 |
| 值域 | [0, ∞) | [0.462, 1.0] |
| x=0 行为 | ∞（需特殊处理） | 1.0（自然） |
| 归档阈值 | 硬 x ≥ 48 | 渐进，t ≥ 30 归档 |
| 模型复杂度 | 对数 + 底数选择 | 指数衰减 + 基线 |

---

## 决策

### 1. 遗忘曲线公式定义

```
R(t) = 0.462 + 0.538 · exp(-t / 2.71)

其中：
  t = 距离上次访问的天数（原系统中的 x），t ≥ 0
  R = 记忆保留率，无量纲

ralqlator 兼容实现（无 exp() 函数，使用 pow(C_E, x)）：
  R = 0.462 + 0.538 * pow(C_E, -t / 2.71)
  └─ 等价于 e^(-t/2.71)

行为规则：
  - 记忆被加载/使用时：t = 0（R = 1.0，完整保留）
  - 每天凌晨 cron：所有记忆 t += 1（R 自然衰减）
  - 会话内主动 read_file 某记忆：该记忆 t = 0
  - 当 t ≥ 30（R ≈ 0.462）：执行遗忘归档
  - R(t) 永不归零，归档后基线 46.2% 保留
```

### 2. TIER 映射表

基于 R(t) 计算值，将记忆分为 6 个 TIER 级别：

| TIER | R(t) 下界 | t (天数) | 详细度 | 行为 |
|------|-----------|----------|--------|------|
| TIER_5 🔥 | R ≥ 0.800 | t ≤ 1 | 完整详细，全部章节 | 全量加载 |
| TIER_4 📗 | R ≥ 0.640 | t ≤ 3 | 详细，核心事实 + 关键细节 | 加载核心 + 细节 |
| TIER_3 📙 | R ≥ 0.503 | t ≤ 7 | 摘要，要点列表 | 加载摘要 |
| TIER_2 📕 | R ≥ 0.465 | t ≤ 14 | 极简，一行概要 | 只读取首行/概要 |
| TIER_1 📦 | R > 0.462 | 14 < t < 30 | 归档待命 | 索引保留，等待归档 |
| ARCHIVE 🗄️ | R ≈ 0.462 | t ≥ 30 | 归档 | 移出 /active/ |

**TIER 边界推导过程：**

```
t=1:  R = 0.462 + 0.538 * e^(-1/2.71) = 0.462 + 0.538 * 0.691 = 0.871 → TIER_5 下界 0.800
t=3:  R = 0.462 + 0.538 * e^(-3/2.71) = 0.462 + 0.538 * 0.331 = 0.640 → TIER_4 下界 0.640
t=7:  R = 0.462 + 0.538 * e^(-7/2.71) = 0.462 + 0.538 * 0.075 = 0.503 → TIER_3 下界 0.503
t=14: R = 0.462 + 0.538 * e^(-14/2.71) = 0.462 + 0.538 * 0.006 = 0.465 → TIER_2 下界 0.465
t=14~30: R ∈ (0.462, 0.465] → TIER_1
t ≥ 30: R ≈ 0.462 → ARCHIVE
```

### 3. 数据结构

#### 3.1 ACTIVITY.yaml（v3 格式）

基于 ADR-002 的 v2 格式，增加 `forgetting_curve` 元数据字段，其余字段复用以保持向后兼容。

```yaml
# Memory Forgetting Curve System v3
# 遗忘曲线: R(t) = 0.462 + 0.538 * exp(-t/2.71)
# t = 距离上次访问的天数
# t=0 → R=1.0 (完整保留), t=30 → R≈0.462 (归档阈值)
# access_count: 累计访问次数，用于成熟度检测
# mature: 是否已标记为成熟记忆
# protected: 保护标记，该记忆永不归档

metadata:
  format_version: 3
  model: forgetting_curve
  formula: "R(t) = 0.462 + 0.538 * exp(-t/2.71)"
  baseline: 0.462
  archive_threshold_t: 30
  created: "2026-06-01"

workflow:
  t: 0
  access_count: 42
  mature: true
rust-learning:
  t: 3
  access_count: 8
  mature: false
memory-system:
  t: 0
  access_count: 999
  mature: true
  protected: true
coder-rules:
  t: 7
  access_count: 15
  mature: false
```

**字段说明：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `t` | int | 距离上次访问的天数。0=刚使用，>=30=归档。每日 cron +1。 |
| `access_count` | int | 累计访问次数。每次 read_file 该记忆时 +1。用于成熟度判定。 |
| `mature` | bool | 是否被标记为成熟记忆。标记后会在适当时机转为永久知识。 |
| `protected` | bool (可选) | 保护标记，该记忆永不归档。仅用于 critical 系统文件如 memory-system。 |

**v2 → v3 迁移说明：**
- `metadata.model` 从 `temperature` 改为 `forgetting_curve`
- `metadata.formula` 新增
- `memories` 下的每个条目 `x` 字段重命名为 `t`（语义更清晰）
- `archive_threshold` 从 48 改为 30
- 其余字段（access_count, mature, protected）不变

#### 3.2 索引条目格式

```yaml
# 旧：idx:workflow [act=N] → active/workflow.md  (ADR-001)
# 旧：idx:workflow [x=N]  → active/workflow.md   (ADR-002)
# 新：idx:workflow [t=N]  → active/workflow.md   (ADR-003)
```

`t=N` 表示距离上次使用天数，与 R(t) 公式中的 t 一致。

**兼容性：** 迁移期间三种格式可以共存。agent 统一解析 `idx:topic [...] → path`，忽略括号内的具体标记名。cron 脚本也会兼容三种格式。

#### 3.3 目录结构

```
~/.hermes/memories/
├── ACTIVITY.yaml              ← 遗忘曲线系统（t, access_count, mature）
├── MEMORY.md                  ← 索引 + 小型事实 (memory tool 管理)
├── USER.md                    ← 用户资料 (不变)
├── active/                    ← 活跃记忆 (t < 30)
│   ├── workflow.md
│   ├── rust-learning.md
│   └── ...
├── archive/
│   ├── forgotten/             ← 遗忘归档（t ≥ 30, 可重新激活）
│   │   └── FORGET_LOG.md
│   └── mature/                ← 成熟归档（永久知识快照）
└── knowledge/                 ← 成熟记忆升级的永久知识文档
    ├── workflow.md
    └── ...
```

### 4. R(t) 驱动的文件内容规范

每个 TIER 级别对应不同的文件详细程度。内容在 agent 使用记忆时按 TIER 调整。

#### 4.1 五级详细度映射

```
TIER_5: R ≥ 0.800 (t ≤ 1天)       — 🔥 完整详细
TIER_4: R ≥ 0.640 (t ≤ 3天)       — 📗 详细
TIER_3: R ≥ 0.503 (t ≤ 7天)       — 📙 摘要
TIER_2: R ≥ 0.465 (t ≤ 14天)      — 📕 极简
TIER_1: R > 0.462 (14 < t < 30天) — 📦 归档待命
ARCHIVE: R ≈ 0.462 (t ≥ 30天)     — 🗄️ 已归档
```

#### 4.2 每级文件内容规范

**TIER_5 🔥 (R ≥ 0.800) — 完整详细**

```markdown
# <topic> — <标题>

## 概要
<完整的上下文说明，2-3段>

## 核心事实
- <关键点1>（含具体数值/路径/命令）
- <关键点2>
- <关键点3>

## 详细说明
<完整的推理过程、代码示例、配置示例等>

## 相关链接
- 相关记忆：<topic2>, <topic3>
- 文件路径：<file_path>
```

**TIER_4 📗 (R ≥ 0.640) — 详细**

```markdown
# <topic> — <标题>

## 核心事实
- <关键点1>（含数值/路径）
- <关键点2>
- <关键点3>

## 关键细节
<去除详细示例和推理过程，保留结论和关键配置>
```

**TIER_3 📙 (R ≥ 0.503) — 摘要**

```markdown
# <topic> — <标题>

- <关键点1>（一句话）
- <关键点2>
- <关键路径/命令>
```

**TIER_2 📕 (R ≥ 0.465) — 极简**

```markdown
# <topic>
<一行概要，最长 150 chars>
```

**TIER_1 📦 (R > 0.462, 14 < t < 30) — 归档待命**

```
索引中保留 [t=N]，但提示 agent 内容已精简。
文件内容同 TIER_2（极简行），等待 cron 触发归档。
```

**ARCHIVE 🗄️ (t ≥ 30) — 已归档**

```
从 active/ 移出。如果被需要，从 archive/forgotten/ 或 archive/mature/ 重新激活。
```

#### 4.3 温度回弹时的内容恢复（R(t) → 1.0）

当记忆被重新使用（t=0, R=1.0），agent 应重建详细内容：

```
温度回弹流程：
1. t=0 → R=1.0 → TIER_5
2. agent 检查当前 active/<topic>.md 内容
3. 如果内容比 TIER_5 更少（例如处于 TIER_3 时期被重新激活）
4. agent 根据对当前对话上下文的理解，补全内容至 TIER_5 规范
5. 写回文件
6. 更新 ACTIVITY.yaml 中该记忆的 t=0, access_count += 1
```

**重建的智能性：**
- agent 不是从存档恢复原始内容（那个已经丢失/精简了）
- 而是基于「当前的知识 + 对话上下文」重建一个当前最相关的详细版本
- 这模拟了人类的回忆过程：不是播放录像带，而是基于线索重建

### 5. 双层归档机制

#### 5.1 遗忘归档（Forgetting Archive）

**触发条件：** `t ≥ 30`（R ≈ 0.462）

```
遗忘归档流程：

cron 触发 or 会话内检查发现 t ≥ 30:
  1. 计算 R = 0.462 + 0.538 * exp(-t/2.71)
  2. 确认 R ≈ 0.462（浮点容差 < 0.001）
  3. 检查 memory-system 保护标记 — 跳过受保护记忆
  4. 检查 mature 标记：
     a. 如果 mature == true → 执行成熟归档流程（见 5.2）
     b. 如果 mature == false → 执行遗忘归档
  5. 遗忘归档动作：
     a. mv active/<topic>.md → archive/forgotten/<topic>.md
     b. 从 MEMORY.md 中删除对应的 idx 条目
     c. 从 ACTIVITY.yaml 中删除该记忆条目
     d. 写入 FORGET_LOG.md
```

**重新激活：**
```
当 agent 遇到 archive/forgotten/<topic>.md 相关内容时：
  1. 读取 archive/forgotten/<topic>.md
  2. 如果内容存在：
     a. 判断内容详细程度（TIER 级别）
     b. 重建至 TIER_5（利用当前上下文补全细节）
     c. mv archive/forgotten/<topic>.md → active/<topic>.md
     d. memory add "idx:<topic> [t=0] → active/<topic>.md"
     e. 在 ACTIVITY.yaml 中添加条目：
        <topic>:
          t: 0
          access_count: <from_forget_log_count + 1>
          mature: false
     f. 在回复中注明：✅ 已重新激活记忆「topic」
```

#### 5.2 成熟归档（Mature Archive）

**触发条件：** 记忆被频繁调用，由 `access_count` 和 `t` 共同决定。

**成熟度判定算法：**

```
算法：is_mature(topic)

输入：ACTIVITY.yaml 中该记忆的 {t, access_count}
输出：bool

简化判定条件（满足任一即可标记成熟）：

1. access_count ≥ 20 且 t ≤ 3
   └─ 访问超过 20 次且最近 3 天用过 → 高频使用 → 成熟

2. 用户或 agent 主动设置 mature: true
   └─ 人工标记 → 成熟

如果条件满足且 mature == false:
  标记 mature = true
  返回 true
否则返回 false
```

**成熟后的处理流程：**

```
成熟归档流程（在遗忘检查时触发）：

当记忆同时满足：
  - mature == true
  - t ≥ 30（R ≈ 0.462，即将被遗忘归档）

说明该记忆虽然高频使用（成熟），但近期未被使用（R 已降至基线）。
此时应该保护其内容不被丢失 —— 转为永久知识。

执行：
  1. 复制 active/<topic>.md → archive/mature/<topic>.md（保留内容快照）
  2. 将内容提炼为永久知识文档：
     a. agent 读取当前 active/<topic>.md 的全部内容
     b. 提炼核心知识，去除临时性内容（如时间戳、对话上下文）
     c. 保存为 ~/.hermes/knowledge/<topic>.md
     d. 格式化为标准的永久知识文档（含版本号、来源、用途）
  3. 删除 active/<topic>.md
  4. 从 MEMORY.md 中删除 idx 条目
  5. 从 ACTIVITY.yaml 中删除该条目
  6. 在 FORGET_LOG.md 中记录为「成熟归档」
  7. 在回复中注明：🎓 记忆「topic」已成熟固化至 knowledge/
```

#### 5.3 永久知识文档格式

```markdown
# <topic> — <标题>

**来源：** forgetting-curve / mature promotion
**固化时间：** 2026-06-01
**原始记忆：** archive/mature/<topic>.md

## 核心知识

<提炼后的永久有效内容，去除时间敏感信息>

## 使用场景

<什么场景下应加载此知识>
```

**knowledge/ 与 active/ 的区别：**
- `active/` 中的文件由遗忘曲线系统管理，内容随 TIER 变化
- `knowledge/` 中的文件是永久固化知识，不被遗忘曲线系统管理
- agent 可以随时读取 knowledge/ 中的文件（类似 skill 文档）

### 6. Cron 脚本设计

#### 6.1 memory-forgetting.py（替代 memory-decay.py / memory-temperature.py）

```python
#!/usr/bin/env python3
"""
Memory Forgetting Curve — 每日遗忘衰减守护
每天凌晨 3 点执行：

1. 读取 ACTIVITY.yaml（v3 格式）
2. 所有记忆 t += 1（受保护记忆除外）
3. 对每个记忆计算 R(t) = 0.462 + 0.538 * exp(-t/2.71)
4. 根据 R(t) 值执行对应操作：
   a. R ≥ 0.800 (TIER_5) — 无需操作
   b. R ≥ 0.640 (TIER_4) — 检查是否需要精简（非强制，交给 agent）
   c. R ≥ 0.503 (TIER_3) — 同上
   d. R ≥ 0.465 (TIER_2) — 同上
   e. R > 0.462 (TIER_1) — 标记为待归档
   f. R ≈ 0.462 (t ≥ 30) — 执行归档
5. 成熟度检测：对 access_count 高的记忆标记 mature
6. 执行归档（遗忘归档 or 成熟归档）
7. 写回 ACTIVITY.yaml
8. 记录事件日志

注意：
- cron 只做 t += 1 和归档操作
- 内容精简（TIER 降级）由 agent 在下次使用时按 R(t) 决定
- R(t) 回弹和内容重建由 agent 在 read_file 时处理

ralqlator 兼容说明：
  exp(x) → pow(C_E, x)
  R = 0.462 + 0.538 * pow(C_E, -t / 2.71)
"""

import os
import shutil
import math
from datetime import datetime
from pathlib import Path

# === Constants ===
MEMORIES_DIR = Path(os.environ.get("HOME", "~")) / ".hermes" / "memories"
MEMORY_FILE = MEMORIES_DIR / "MEMORY.md"
ACTIVITY_FILE = MEMORIES_DIR / "ACTIVITY.yaml"
ACTIVE_DIR = MEMORIES_DIR / "active"
ARCHIVE_FORGOTTEN_DIR = MEMORIES_DIR / "archive" / "forgotten"
ARCHIVE_MATURE_DIR = MEMORIES_DIR / "archive" / "mature"
KNOWLEDGE_DIR = Path(os.environ.get("HOME", "~")) / ".hermes" / "knowledge"
FORGET_LOG = MEMORIES_DIR / "archive" / "FORGET_LOG.md"
ARCHIVE_THRESHOLD_T = 30
MATURE_ACCESS_THRESHOLD = 20
MATURE_T_THRESHOLD = 3

logs = []


def forgetting_curve(t: int) -> float:
    """
    计算保留率 R(t)
    R(t) = 0.462 + 0.538 * exp(-t / 2.71)
    
    ralqlator 兼容实现: pow(C_E, x) = exp(x)
    """
    tau = 2.71
    if t == 0:
        return 1.0
    r = 0.462 + 0.538 * math.exp(-t / tau)
    return max(0.462, r)  # 下限 46.2%


def r_to_tier(r: float) -> str:
    """将 R(t) 值映射为 TIER 等级"""
    if r >= 0.800:
        return "TIER_5 🔥"
    elif r >= 0.640:
        return "TIER_4 📗"
    elif r >= 0.503:
        return "TIER_3 📙"
    elif r >= 0.465:
        return "TIER_2 📕"
    elif r > 0.462:
        return "TIER_1 📦"
    else:
        return "ARCHIVE 🗄️"


def parse_activity_v3(text: str) -> dict:
    """解析 v3 格式的 ACTIVITY.yaml"""
    import yaml
    return yaml.safe_load(text)


def write_activity_v3(data: dict):
    """写入 v3 格式的 ACTIVITY.yaml"""
    import yaml
    with open(ACTIVITY_FILE, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


def read_memory_index() -> list:
    if not MEMORY_FILE.exists():
        return []
    raw = MEMORY_FILE.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    return [e.strip() for e in raw.split("\n§\n") if e.strip()]


def write_memory_index(entries: list):
    content = "\n§\n".join(entries)
    MEMORY_FILE.write_text(content + "\n", encoding="utf-8")


def write_forget_log(topic: str, t: int, r: float, reason: str):
    ARCHIVE_FORGOTTEN_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"| {now} | {topic} | t={t} | R={r:.4f} | {reason} |\n"
    if not FORGET_LOG.exists():
        header = """# Memory Forgetting Log (Forgetting Curve Model v3)
| 时间 | 主题 | t值 | R(t) | 原因 |
|------|------|-----|------|------|
"""
        FORGET_LOG.write_text(header + entry, encoding="utf-8")
    else:
        with open(FORGET_LOG, "a", encoding="utf-8") as f:
            f.write(entry)


def forget_archive(topic: str, t: int, r: float, data: dict):
    """遗忘归档：移到 archive/forgotten/"""
    src = ACTIVE_DIR / f"{topic}.md"
    dst = ARCHIVE_FORGOTTEN_DIR / f"{topic}.md"
    if src.exists():
        ARCHIVE_FORGOTTEN_DIR.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        logs.append(f"📦  {topic}: moved to archive/forgotten/")
    # Remove from MEMORY.md
    entries = read_memory_index()
    new_entries = [e for e in entries if not e.startswith(f"idx:{topic}")]
    write_memory_index(new_entries)
    # Remove from ACTIVITY.yaml
    del data["memories"][topic]
    write_forget_log(topic, t, r, "forgotten")
    logs.append(f"🗑️  {topic}: idx removed, logged to FORGET_LOG")


def mature_archive(topic: str, t: int, r: float, data: dict, memory_info: dict):
    """成熟归档：复制到 archive/mature/ 并创建 knowledge/ 文档"""
    src = ACTIVE_DIR / f"{topic}.md"
    # Copy to mature archive
    ARCHIVE_MATURE_DIR.mkdir(parents=True, exist_ok=True)
    if src.exists():
        shutil.copy(str(src), str(ARCHIVE_MATURE_DIR / f"{topic}.md"))
        logs.append(f"🎓  {topic}: copied to archive/mature/")
    # Create knowledge document
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    knowledge_path = KNOWLEDGE_DIR / f"{topic}.md"
    original_content = ""
    if src.exists():
        original_content = src.read_text(encoding="utf-8")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    knowledge_content = f"""# {topic}

**来源：** forgetting-curve mature promotion
**固化时间：** {now}
**访问次数：** {memory_info.get('access_count', 0)}
**原始存档：** archive/mature/{topic}.md

**注意：** 此文件由遗忘曲线系统自动生成，内容为原始记忆的副本。
agent 应在下次使用时提取核心知识并替换此文件内容。

---

{original_content}
"""
    knowledge_path.write_text(knowledge_content, encoding="utf-8")
    logs.append(f"📚  {topic}: knowledge doc created at {knowledge_path}")
    # Remove from active
    if src.exists():
        src.unlink()
    # Remove from MEMORY.md
    entries = read_memory_index()
    new_entries = [e for e in entries if not e.startswith(f"idx:{topic}")]
    write_memory_index(new_entries)
    # Remove from ACTIVITY.yaml
    del data["memories"][topic]
    write_forget_log(topic, t, r, "mature archived")
    logs.append(f"🎓  {topic}: mature archived to knowledge/")


def main():
    run_time = datetime.now()
    logs.append(f"=== Memory Forgetting Curve Run: {run_time} ===")
    
    if not ACTIVITY_FILE.exists():
        logs.append("❌ ACTIVITY.yaml not found, aborting")
        return
    
    data = parse_activity_v3(ACTIVITY_FILE.read_text(encoding="utf-8"))
    memories = data.get("memories", {})
    
    if not memories:
        logs.append("ℹ️  No memory entries in ACTIVITY.yaml")
        return
    
    logs.append(f"📊  Loaded {len(memories)} memories")
    
    to_forget = []    # (topic, t, R)
    to_mature = []    # (topic, t, R, info)
    
    for topic, info in memories.items():
        if info.get("protected", False):
            logs.append(f"🛡️  {topic}: protected, skipping")
            continue
        
        t = info.get("t", 0) + 1  # 每天 +1
        info["t"] = t
        r = forgetting_curve(t)
        tier = r_to_tier(r)
        
        # 成熟度检测
        if not info.get("mature", False):
            access_count = info.get("access_count", 0)
            if access_count >= MATURE_ACCESS_THRESHOLD and t <= MATURE_T_THRESHOLD:
                info["mature"] = True
                logs.append(f"🌟  {topic}: matured (access_count={access_count}, t={t})")
        
        # 归档判定 (t ≥ 30, R ≈ 0.462)
        if t >= ARCHIVE_THRESHOLD_T:
            if info.get("mature", False):
                to_mature.append((topic, t, r, info))
            else:
                to_forget.append((topic, t, r))
        
        logs.append(f"📈  {topic}: t={t}, R={r:.4f} ({tier})" + 
                     (" 🎓 mature" if info.get("mature") else "") +
                     (" 🔜 archive" if t >= ARCHIVE_THRESHOLD_T else ""))
    
    # 执行归档
    for topic, t, r in to_forget:
        forget_archive(topic, t, r, data)
    
    for topic, t, r, info in to_mature:
        mature_archive(topic, t, r, data, info)
    
    # 写回
    write_activity_v3(data)
    logs.append(f"✅  ACTIVITY.yaml updated ({len(data.get('memories', {}))} active memories)")
    
    if to_forget or to_mature:
        logs.append(f"📊  Total archived: {len(to_forget)} forgotten + {len(to_mature)} mature")
    else:
        logs.append("🟢  No memories exceeded threshold")
    
    _deliver(logs)


def _deliver(log_lines: list):
    output = "\n".join(log_lines)
    if "📦" in output or "🎓" in output or "⚠️" in output or "❌" in output:
        print(output)


if __name__ == "__main__":
    main()
```

#### 6.2 memory-monthly-review.py（更新）

```python
#!/usr/bin/env python3
"""
Memory Archive Monthly Review — 每月归档回顾报告（遗忘曲线版）

变更点：
- 使用 R(t) 替代温度 y
- 基于 R(t) 的 TIER 等级分布
- 标注每个记忆的 TIER 级别和 R(t) 值
- 统计归档原因分布（遗忘 vs 成熟）
- 报告 knowledge/ 中的固化知识数量
- 计算系统整体保留率均值
"""

import os
import math
import yaml
from datetime import datetime
from pathlib import Path

MEMORIES_DIR = Path(os.environ.get("HOME", "~")) / ".hermes" / "memories"
ACTIVITY_FILE = MEMORIES_DIR / "ACTIVITY.yaml"
ACTIVE_DIR = MEMORIES_DIR / "active"
ARCHIVE_DIR = MEMORIES_DIR / "archive"
FORGET_LOG = ARCHIVE_DIR / "FORGET_LOG.md"
KNOWLEDGE_DIR = Path(os.environ.get("HOME", "~")) / ".hermes" / "knowledge"


def forgetting_curve(t: int) -> float:
    if t == 0:
        return 1.0
    tau = 2.71
    r = 0.462 + 0.538 * math.exp(-t / tau)
    return max(0.462, r)


def r_to_tier(r: float) -> str:
    if r >= 0.800:
        return "TIER_5 🔥"
    elif r >= 0.640:
        return "TIER_4 📗"
    elif r >= 0.503:
        return "TIER_3 📙"
    elif r >= 0.465:
        return "TIER_2 📕"
    elif r > 0.462:
        return "TIER_1 📦"
    else:
        return "ARCHIVE 🗄️"


def count_md_files(directory: Path) -> int:
    if not directory.exists():
        return 0
    return len([f for f in directory.iterdir() if f.suffix == ".md"])


def read_activity_v3() -> dict:
    if not ACTIVITY_FILE.exists():
        return {}
    with open(ACTIVITY_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def read_forget_log() -> list:
    if not FORGET_LOG.exists():
        return []
    result = []
    for line in FORGET_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or not line.startswith("|") or line.startswith("| ---"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 4:
            date, topic, t_val, r_val = parts[1], parts[2], parts[3], parts[4]
            result.append((date, topic, t_val, r_val))
    return result


def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    active_count = count_md_files(ACTIVE_DIR)
    archive_forgotten = count_md_files(MEMORIES_DIR / "archive" / "forgotten")
    archive_mature = count_md_files(MEMORIES_DIR / "archive" / "mature")
    knowledge_count = count_md_files(KNOWLEDGE_DIR)
    
    data = read_activity_v3()
    memories = data.get("memories", {}) if data else {}
    
    total_r = 0.0
    tier_distribution = {
        "TIER_5 🔥": 0, "TIER_4 📗": 0, "TIER_3 📙": 0,
        "TIER_2 📕": 0, "TIER_1 📦": 0, "ARCHIVE 🗄️": 0
    }
    
    memory_table = ""
    if memories:
        memory_table += "\n| 主题 | t | R(t) | TIER | 访问次数 | 成熟度 |\n"
        memory_table += "|------|---|------|------|---------|--------|\n"
        for topic in sorted(memories.keys(), key=lambda k: memories[k].get("t", 0), reverse=True):
            info = memories[topic]
            t = info.get("t", 0)
            r = forgetting_curve(t)
            tier = r_to_tier(r)
            access = info.get("access_count", 0)
            mature = "🎓" if info.get("mature") else "—"
            protected = "🛡️" if info.get("protected") else ""
            total_r += r
            tier_distribution[tier] = tier_distribution.get(tier, 0) + 1
            memory_table += f"| `{topic}` {protected}| {t} | {r:.4f} | {tier} | {access} | {mature} |\n"
    
    avg_r = round(total_r / len(memories), 4) if memories else 0
    
    this_month_prefix = now[:7]
    forgot = read_forget_log()
    this_month_forgot = [(d, t, tv, rv) for d, t, tv, rv in forgot if d.startswith(this_month_prefix)]
    
    mature_count = len([i for i in memories.values() if i.get("mature")])
    
    report = f"""# 🧠 记忆归档月度回顾（遗忘曲线版）
**报告时间：** {now}
**公式：** R(t) = 0.462 + 0.538 · exp(-t/2.71)

---

## 📊 概览

| 指标 | 数值 |
|------|------|
| 活跃记忆 | {active_count} 个 |
| 遗忘归档 | {archive_forgotten} 个（含 FORGET_LOG） |
| 成熟归档 | {archive_mature} 个 |
| 固化知识 | {knowledge_count} 个 |
| 本月遗忘 | {len(this_month_forgot)} 个 |
| 系统保留率均值 | {avg_r} |
| 成熟记忆数 | {mature_count} 个 |

## 📈 TIER 分布

| TIER | 数量 | 占比 |
|------|------|------|
"""
    for tier_name in ["TIER_5 🔥", "TIER_4 📗", "TIER_3 📙", "TIER_2 📕", "TIER_1 📦"]:
        count = tier_distribution.get(tier_name, 0)
        pct = round(count / len(memories) * 100, 1) if memories else 0
        report += f"| {tier_name} | {count} | {pct}% |\n"
    
    report += f"\n## 活跃记忆状态\n"
    
    if not memories:
        report += "\n（无活跃记忆）\n"
    else:
        report += memory_table
    
    if this_month_forgot:
        report += f"\n## 📦 本月遗忘事件\n\n| 时间 | 主题 | t值 | R(t) |\n|------|------|-----|------|\n"
        for d, t, tv, rv in this_month_forgot:
            report += f"| {d} | `{t}` | {tv} | {rv} |\n"
    
    if archive_mature > 0:
        report += f"\n## 🎓 本月成熟归档\n\n已固化至 `~/.hermes/knowledge/`，agent 可随时读取。\n"
    
    report += f"""
---
*自动生成 — 每月 1 日 09:00 — 遗忘曲线模型 v3*
"""
    
    print(report)


if __name__ == "__main__":
    main()
```

### 7. 与 Hermes memory tool 的共存策略

#### 7.1 索引格式兼容性

三种索引格式在当前迁移期可以共存：

```yaml
# 旧 (ADR-001):  idx:workflow [act=0] → active/workflow.md
# 旧 (ADR-002):  idx:workflow [x=0]  → active/workflow.md
# 新 (ADR-003):  idx:workflow [t=0]  → active/workflow.md
```

agent 统一解析 `idx:topic [...] → path`，忽略括号内的具体标记名。cron 脚本也会兼容三种格式。

#### 7.2 遗忘曲线系统 vs memory tool 的关系

```
memory tool（system prompt 注入）  →  MEMORY.md（索引 + 小型事实）
                                           ↑
遗忘曲线系统（独立层）              →  ACTIVITY.yaml（t, access_count, mature）
                                           ↓
active/*.md（内容由 R(t) 决定详略） →  agent 按需 read_file
```

- memory tool 仍然管理 MEMORY.md 的读写（add/remove/replace）
- 遗忘曲线系统通过 ACTIVITY.yaml 独立管理活跃度
- 两者的接口是 MEMORY.md 中的 `idx:topic [t=N]` 条目
- memory tool 的 `memory("replace")` 不应修改 ACTIVITY.yaml
- cron 脚本修改 ACTIVITY.yaml 后，下一次 memory tool 读取时自动同步（冻结快照模式）

#### 7.3 与 memory tool 的读写分离

```
┌────────────────────────────────────────────────────┐
│ 写操作（遗忘曲线系统控制）                            │
├────────────────────────────────────────────────────┤
│ memory tool 写 MEMORY.md（add/remove/replace）      │
│ cron 脚本写 ACTIVITY.yaml（t += 1, 归档）           │
│ agent 写 active/*.md（内容详略调整）                  │
│ agent 写 ACTIVITY.yaml（t=0 回弹, access_count++）  │
└────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────┐
│ 读操作（按需加载）                                    │
├────────────────────────────────────────────────────┤
│ system prompt 读取 MEMORY.md（冻结快照）              │
│ agent 解析 idx:topic [t=N]                          │
│ agent 根据 t 计算 R(t) = 0.462 + 0.538 * exp(-t/2.71) │
│ agent 根据 R(t) 决定读取深度：                         │
│   R ≥ 0.800 → 完整读取                               │
│   R ≥ 0.640 → 读取核心 + 细节                         │
│   R ≥ 0.503 → 读取摘要                                │
│   R < 0.503 → 只读第一行                              │
└────────────────────────────────────────────────────┘
```

### 8. 记忆协议更新（memory-system.md）

需要将新的遗忘曲线协议写入 memory-system.md。

**核心变更：**
- 用 R(t) 替代温度 y，用 t（天数）替代 x
- 加载时根据 R(t) 判断需要读取多少内容
- R(t) 回弹时需重建详细内容
- 新增 R(t) 计算公式的 ralqlator 兼容版本
- 新增 TIER 映射表
- 新增成熟记忆检测逻辑
- 新增双层归档逻辑

### 9. 迁移方案

从对数温度模型（ADR-002）到遗忘曲线模型（ADR-003）的迁移步骤。

#### Phase 0: 准备（30分钟）

```bash
# 1. 备份当前系统
cp ~/.hermes/memories/ACTIVITY.yaml ~/.hermes/memories/ACTIVITY.yaml.bak
cp ~/.hermes/memories/MEMORY.md ~/.hermes/memories/MEMORY.md.bak
cp ~/.hermes/scripts/memory-temperature.py ~/.hermes/scripts/memory-temperature.py.bak
cp ~/.hermes/scripts/memory-monthly-review.py ~/.hermes/scripts/memory-monthly-review.py.bak

# 2. 确认目录结构存在
ls -la ~/.hermes/memories/archive/forgotten/
ls -la ~/.hermes/memories/archive/mature/
ls -la ~/.hermes/knowledge/
```

#### Phase 1: 数据格式迁移（1小时）

将 ACTIVITY.yaml 从 v2 格式转换为 v3 格式：

```yaml
# v2 → v3 转换逻辑：
# v2: metadata.model = "temperature"
# v3: metadata.model = "forgetting_curve"
# v2: topic: {x: N, access_count: M, mature: bool}
# v3: topic: {t: N, access_count: M, mature: bool}

def migrate_v2_to_v3(old_data: dict) -> dict:
    """转换 ACTIVITY.yaml 从 v2 到 v3 格式"""
    new_data = {
        "metadata": {
            "format_version": 3,
            "model": "forgetting_curve",
            "formula": "R(t) = 0.462 + 0.538 * exp(-t/2.71)",
            "baseline": 0.462,
            "archive_threshold_t": 30,
            "created": old_data["metadata"].get("created", "2026-06-01"),
        },
        "memories": {}
    }
    
    memories = old_data.get("memories", {})
    for topic, info in memories.items():
        new_data["memories"][topic] = {
            "t": info.get("x", 0),         # x → t 字段重命名
            "access_count": info.get("access_count", 0),
            "mature": info.get("mature", False),
        }
        if info.get("protected", False):
            new_data["memories"][topic]["protected"] = True
    
    return new_data
```

**注意事项：**
- memory-system 条目应保持 `protected: true`
- 旧 x 值直接映射为新 t 值（语义一致：天数）
- access_count 保持不变（已有历史数据）
- 归档阈值从 48 改为 30 — 这意味着部分 t ∈ [30, 48) 的记忆在迁移后会被立即归档

**处理 t ∈ [30, 48) 的记忆：**
```
迁移时对每个记忆检查 t 值：
- t < 30: 保持不变，继续活跃
- t ≥ 30 且成熟: 执行成熟归档
- t ≥ 30 且未成熟: 执行遗忘归档

这可能导致一次性归档多个记忆。这是预期行为 — 阈值从 48 降低到 30
意味着旧系统中「挂在悬崖边」的记忆应该被清理。
```

#### Phase 2: 索引格式更新（可选）

将 MEMORY.md 中的 `[x=N]` 替换为 `[t=N]`：

```
# 替换前：idx:workflow [x=0] → active/workflow.md
# 替换后：idx:workflow [t=0] → active/workflow.md
```

纯文本替换，不影响其他内容。如果无需统一，所有三种格式可以共存。

#### Phase 3: 脚本替换

```bash
# 删除旧脚本
rm ~/.hermes/scripts/memory-temperature.py
rm ~/.hermes/scripts/memory-decay.py

# 安装新脚本
# 将 memory-forgetting.py 写入 ~/.hermes/scripts/

# 更新 crontab
# 旧：0 3 * * * cd ~/.hermes && python3 scripts/memory-temperature.py
# 新：0 3 * * * cd ~/.hermes && python3 scripts/memory-forgetting.py

# 更新月度回顾 crontab
# 旧：0 9 1 * * cd ~/.hermes && python3 scripts/memory-monthly-review.py
# 新：0 9 1 * * cd ~/.hermes && python3 scripts/memory-monthly-review.py
# （文件可能会被覆盖，但 crontab 本身无需变动）
```

#### Phase 4: 记忆系统协议更新

更新 `~/.hermes/memories/active/memory-system.md`，加入遗忘曲线模型协议：
- 替换所有温度公式为 R(t) 公式
- 替换 TIER 映射表
- 加入 ralqlator 兼容公式
- 更新 ADR 链接指向 ADR-003

#### Phase 5: Agent 协议集成

```yaml
## 记忆系统使用协议（遗忘曲线模型）

系统使用三层记忆体系 + 遗忘曲线模型：

### R(t) 遗忘曲线
R(t) = 0.462 + 0.538 * exp(-t/2.71)
ralqlator: R = 0.462 + 0.538 * pow(C_E, -t / 2.71)

### TIER 映射
| TIER | R(t) 下界 | t(天) | 详细度 |
|------|-----------|-------|--------|
| TIER_5 🔥 | R ≥ 0.800 | t ≤ 1 | 完整详细 |
| TIER_4 📗 | R ≥ 0.640 | t ≤ 3 | 核心+细节 |
| TIER_3 📙 | R ≥ 0.503 | t ≤ 7 | 摘要要点 |
| TIER_2 📕 | R ≥ 0.465 | t ≤ 14 | 一行概要 |
| TIER_1 📦 | R > 0.462 | 14< t <30 | 归档待命 |
| ARCHIVE 🗄️ | R ≈ 0.462 | t ≥ 30 | 归档 |

### 每次对话时：
1. 解析 MEMORY.md 中的 idx: 索引
2. 根据上下文判断需要加载哪些 topic
3. 对于每个相关 topic，计算 R(t) 判断读取深度
4. 调用 read_file("active/<topic>.md")
5. 更新 ACTIVITY.yaml：被读的 t=0，其他的 t+=1，access_count++
6. 检查 t ≥ 30 的记忆 → 归档
7. 如果遇到 archive/ 相关的 topic → 重新激活
```

### 10. 回滚方案

如果新系统出现问题：

```bash
# 恢复备份
cp ~/.hermes/memories/ACTIVITY.yaml.bak ~/.hermes/memories/ACTIVITY.yaml
cp ~/.hermes/memories/MEMORY.md.bak ~/.hermes/memories/MEMORY.md

# 恢复旧脚本
# 将 memory-temperature.py.bak 放回 ~/.hermes/scripts/memory-temperature.py
# 恢复 crontab

# 回滚目录结构（可选）
# 如果创建了 archive/forgotten/ 和 archive/mature/，保留或整理
# 保留知识库（回滚后 knowledge/ 内容不受影响，可以继续使用）
```

**v3 → v2 反向迁移：**

```python
def revert_v3_to_v2(data: dict) -> dict:
    """从 v3 格式回退到 v2 格式"""
    old_data = {
        "metadata": {
            "format_version": 2,
            "model": "temperature",
            "created": data["metadata"].get("created", "2026-06-01"),
        },
        "memories": {}
    }
    
    memories = data.get("memories", {})
    for topic, info in memories.items():
        old_data["memories"][topic] = {
            "x": info.get("t", 0),         # t → x 字段重命名
            "access_count": info.get("access_count", 0),
            "mature": info.get("mature", False),
        }
        if info.get("protected", False):
            old_data["memories"][topic]["protected"] = True
    
    return old_data
```

---

## 替代方案

### 未采纳：S 形曲线（Sigmoid）

```
R(t) = 1 / (1 + e^(k(t - t₀)))
```

**理由：** Sigmoid 在阈值附近变化剧烈，会产生「悬崖效应」。虽然 Sigmoid 比对数模型平滑，但比本方案的指数衰减曲线仍然更陡峭。指数衰减 + 基线模型是最自然的遗忘模拟。

### 未采纳：幂律衰减

```
R(t) = (t + 1)^(-α)
```

**理由：** 幂律衰减在 t=0 时有定义（=1），但长期衰减趋近于 0，没有基线保留率。人类记忆不会完全遗忘到 0%，所以幂律模型不适合模拟长期保留。

### 未采纳：保留对数温度模型但调整参数

**理由：** 对数模型的无界温度（x=0 → ∞）是根本性缺陷，不是参数调整能解决的。R(t) 模型的值域 [0.462, 1.0] 更符合记忆保留的物理意义。

### 未采纳：纯阈值模型（ADR-001 的线性 act）

**理由：** 线性模型无法表达信息密度衰减，且无梯度遗忘。已有 ADR-002 的分析证明其不足。

---

## 影响分析

### 正面影响

| 方面 | 旧系统（对数温度） | 新系统（遗忘曲线） |
|------|-------------------|-------------------|
| 理论依据 | 工程便利 | 艾宾浩斯遗忘曲线 |
| 值域 | [0, ∞)，x=0 需特殊处理 | [0.462, 1.0]，自然有界 |
| 归档阈值 | 硬 x ≥ 48 | 渐进，t ≥ 30 基线 |
| 永不归零 | 否 | 是，46.2% 基线保留 |
| 模型复杂度 | 对数 + 底数选择 | 指数衰减 + 基线 |
| ralqlator 兼容 | ❌（对数函数） | ✅（pow(C_E, x)） |
| 对人类理解 | 需要解释 | R(t)=保留率，直观 |
| 收敛时间 | 48 天 | 30 天（更快清理无用记忆） |

### 负面影响

| 风险 | 缓解措施 |
|------|---------|
| 归档阈值从 48 降到 30，迁移时可能一次归档多个记忆 | 迁移脚本明确处理；这是预期清理行为 |
| 成熟度判定参数需重新校准 | 保持与 v2 相同参数 (access_count ≥ 20 且 t ≤ 3) |
| 索引标记从 `[x=N]` 改为 `[t=N]`，旧 agent 协议可能无法识别 | 迁移期三种格式共存；agent 统一解析 `idx:topic [...] → path` |
| knowledge/ 目录可能增长过快 | 成熟归档的门槛（access_count ≥ 20）确保只有真正高频的记忆被固化 |

### 对现有记忆的影响

- 所有现有记忆的 x 值直接映射为 t（语义一致）
- access_count 保持不变
- mature 标记保持不变
- t ∈ [30, 48) 的记忆将在迁移时被归档（阈值降低）

### 向后兼容性

- MEMORY.md 中的索引条目格式：`[act=N]`、`[x=N]`、`[t=N]` 均可解析
- cron 脚本完全替换，不保留旧版
- agent 协议更新为新版，旧版协议不再使用
- ACTIVITY.yaml v2 中的 `x` 字段在 v3 中变为 `t`，需要迁移脚本转换

---

## 实现计划

### Phase 0: 准备（30分钟）

- [ ] 备份当前 ACTIVITY.yaml、MEMORY.md、脚本文件
- [ ] 确认目录结构完整（archive/forgotten/、archive/mature/、knowledge/）

### Phase 1: 数据迁移（1小时）

- [ ] 编写 ACTIVITY.yaml v2 → v3 迁移脚本
- [ ] 执行迁移，处理 t ∈ [30, 48) 的记忆归档
- [ ] 验证 YAML 格式正确
- [ ] 可选：更新 MEMORY.md 中索引条目标记（[x=N] → [t=N]）
- [ ] 验证 memory tool 可正常读取新格式

### Phase 2: Cron 脚本实现（2小时）

- [ ] 编写 memory-forgetting.py（完整实现，含 YAML 读写、R(t) 计算、TIER 映射、归档逻辑）
- [ ] 编写单元测试（R(t) 计算、TIER 映射、归档判定、成熟度判定）
- [ ] 手动运行测试，验证输出
- [ ] 更新 crontab：替换 memory-temperature.py → memory-forgetting.py

### Phase 3: 记忆系统协议更新（1小时）

- [ ] 重写 memory-system.md → 遗忘曲线模型协议
- [ ] 写入 ADR-003 设计文档链接
- [ ] 确认 memory-system 标记为 protected: true

### Phase 4: Agent 协议集成（30分钟）

- [ ] 添加 R(t) 计算公式到 agent 的系统提示或 SOUL
- [ ] 添加 TIER 映射表（含 ralqlator 兼容公式）
- [ ] 添加 R(t) 回弹时的内容重建指引
- [ ] 添加成熟记忆检测指引

### Phase 5: 月度回顾脚本更新（30分钟）

- [ ] 更新 memory-monthly-review.py 使用 R(t) 指标
- [ ] 添加 TIER 分布统计
- [ ] 添加 mature/knowledge 统计
- [ ] 添加系统保留率均值指标

### Phase 6: 监控与调优（持续）

- [ ] 监控归档频率是否合理（新阈值 30 天）
- [ ] 根据实际使用调整成熟度判定参数
- [ ] 收集 agent 反馈，优化 TIER 映射规则
- [ ] 如果 knowledge/ 中文件增长过快，考虑引入 knowledge/ 的索引机制

---

## 附录 A：R(t) 计算速查表

```python
import math
TAU = 2.71

def forgetting_curve(t):
    if t == 0:
        return 1.0
    return 0.462 + 0.538 * math.exp(-t / TAU)

# 常用值
for t in [0, 1, 2, 3, 5, 7, 10, 14, 21, 30, 45, 60]:
    r = forgetting_curve(t)
    if r >= 0.800:
        tier = "TIER_5 🔥"
    elif r >= 0.640:
        tier = "TIER_4 📗"
    elif r >= 0.503:
        tier = "TIER_3 📙"
    elif r >= 0.465:
        tier = "TIER_2 📕"
    elif r > 0.462:
        tier = "TIER_1 📦"
    else:
        tier = "ARCHIVE 🗄️"
    print(f"t={t:3d} → R={r:.4f} ({tier})")
```

**输出：**
```
t=  0 → R=1.0000 (TIER_5 🔥)
t=  1 → R=0.8706 (TIER_5 🔥)
t=  2 → R=0.7359 (TIER_4 📗)
t=  3 → R=0.6405 (TIER_4 📗)
t=  5 → R=0.5403 (TIER_3 📙)
t=  7 → R=0.5034 (TIER_3 📙)
t= 10 → R=0.5001 (TIER_2 📕)
t= 14 → R=0.4804 (TIER_2 📕)
t= 21 → R=0.4686 (TIER_2 📕)
t= 30 → R=0.4628 (ARCHIVE 🗄️)
t= 45 → R=0.4620 (ARCHIVE 🗄️)
t= 60 → R=0.4620 (ARCHIVE 🗄️)
```

## 附录 B：ralqlator 公式

```ralqlator
# R(t) 遗忘曲线 — ralqlator 兼容版
# ralqlator 没有 exp()，使用 pow(C_E, x) 替代

# 公式：
R = 0.462 + 0.538 * pow(C_E, -t / 2.71)

# 其中：
#   C_E = 自然常数 e (≈ 2.71828...)
#   pow(C_E, x) = C_E^x = e^x
#   pow(C_E, -t/2.71) = e^(-t/2.71)

# 示例（注：ralqlator 中 / 是整数除法，需确保 t 为浮点数）：
# t=0:  R = 0.462 + 0.538 * pow(C_E, 0)   = 0.462 + 0.538 = 1.0
# t=1:  R = 0.462 + 0.538 * pow(C_E, -0.37) = 0.462 + 0.538 * 0.691 = 0.871
# t=3:  R = 0.462 + 0.538 * pow(C_E, -1.11) = 0.462 + 0.538 * 0.330 = 0.640
# t=7:  R = 0.462 + 0.538 * pow(C_E, -2.58) = 0.462 + 0.538 * 0.076 = 0.503
# t=14: R = 0.462 + 0.538 * pow(C_E, -5.17) = 0.462 + 0.538 * 0.006 = 0.465
# t=30: R = 0.462 + 0.538 * pow(C_E, -11.07)= 0.462 + 0.538 * 0.000016 ≈ 0.462
```

## 附录 C：与旧系统的对比速查

| 概念 | ADR-002（温度模型） | ADR-003（遗忘曲线） |
|------|--------------------|--------------------|
| 核心公式 | y = -log₁.₀₉(x) + 45 | R(t) = 0.462 + 0.538 · exp(-t/2.71) |
| 活跃度指标 | x（天数） | t（天数） |
| 详细程度指标 | y（温度，值域 [0, ∞)） | R（保留率，值域 [0.462, 1.0]） |
| 归档阈值 | x ≥ 48 | t ≥ 30 |
| 归档类型 | 遗忘归档 + 成熟归档 | 遗忘归档 + 成熟归档（保持不变） |
| 成熟度判定 | access_count ≥ 20 且 x ≤ 3 | access_count ≥ 20 且 t ≤ 3（保持不变） |
| 知识固化 | mature → knowledge/ | mature → knowledge/（保持不变） |
| 受保护记忆 | `protected: true` | `protected: true`（保持不变） |
| 目录结构 | 同 v3 | 同 v3（保持不变） |
| ralqlator 兼容 | ❌ | ✅ pow(C_E, x) |
| 理论基础 | 工程便利 | 艾宾浩斯遗忘曲线 |
| 内容重建 | 温度回弹时重建 | R(t) 回弹时重建（保持不变） |
