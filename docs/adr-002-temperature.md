# ADR-002: 温度模型记忆系统（替代线性活跃度）

**状态：** 已接受
**阶段：** 📐 设计完成（待实现）
**时间：** 2026-05-21
**替代：** ADR-001 (ADR-memory-index-forgetting.md)

---

## 上下文

### 原系统（ADR-001）的问题

原系统使用线性活跃度模型：

```
act += 1（每日 cron）
act > 30 → 归档
```

三个根本缺陷：

1. **无梯度遗忘** — act 从 0 到 30 之间没有行为差异。第 1 天和第 29 天的记忆在 prompt 中占用完全一样的空间，这在资源有限的情况下效率低下。

2. **线性模型无法表达「信息密度衰减」** — 人类记忆不是线性衰减的：刚接触时记忆最清晰（高信息密度），随后快速衰减，然后缓慢衰减到遗忘。线性模型无法捕获这种对数衰减特征。

3. **阈值悬崖** — act=29 时完整保留，act=31 时突然完全消失。没有中间状态（摘要、极简）。这是「全有或全无」的二值遗忘，浪费了可以渐进压缩的机会。

### 新需求

- 记忆内容应根据「多久未使用」自动调整详细程度
- 温度（详细程度）应随活跃度对数衰减，而非线性衰减
- 支持双层归档：遗忘归档（自然遗忘）和成熟归档（频繁使用后固化）
- 内容压缩是非破坏性的、可逆的（重新使用时自动还原）

### 数学基础

温度公式选择对数函数（底数 1.09），原因：

```
y = -log₁.₀₉(x) + 45
```

| 天数 x | 温度 y | 物理意义 |
|--------|--------|---------|
| 0 | → ∞ | 刚使用，记忆最详细 |
| 1 | 45.0 | 使用后第 1 天 |
| 3 | 32.3 | 快速衰减期 |
| 7 | 22.4 | 一周后，中等详细 |
| 14 | 14.4 | 两周后，概要级 |
| 21 | 9.7 | 三周后，极简 |
| 30 | 5.5 | 接近遗忘 |
| 45 | 0.8 | 归档边缘 |
| 48 | ≈ 0 | **归档阈值** |

选择底数 1.09 的理由：
- 使归档阈值落在 x=48（约 48 天），是一个方便的人类遗忘周期
- 对数衰减曲线在 x=[0,7] 区间下降最快（模拟人类短期记忆快速衰减），在 x=[7,48] 区间缓慢下降（模拟长期记忆稳定衰减）
- y=45 在 x=1 的整数点，便于计算

---

## 决策

### 1. 温度公式定义

```
y = -log₁.₀₉(x) + 45

其中：
  x = 活跃度（距离上次访问的天数），x ≥ 0
  y = 温度（记忆详细程度），y ≥ 0

行为规则：
  - 记忆被加载/使用时：x = 0（温度回弹至 ∞）
  - 每天凌晨 cron：所有记忆 x += 1（自然遗忘）
  - 会话内主动 read_file 某记忆：该记忆 x = 0
  - 当 y < 2（约 x > 42）：开始归档准备
  - 当 y ≈ 0（x ≥ 48）：执行归档
```

**特殊值处理：**
- `x=0` 时，公式中 `log₁.₀₉(0) → -∞`，因此 `y → +∞`。实际实现中，将 x=0 映射为 `y = 999`（表示「极致详细」）。在索引中显示为 `y=∞`。

### 2. 数据结构

#### 2.1 ACTIVITY.yaml（新格式）

```yaml
# Memory Temperature System v2
# 温度公式: y = -log₁.₀₉(x) + 45
# x = 距离上次访问的天数
# x=0 → y=∞ (极致详细), x=48 → y≈0 (归档阈值)
# access_count: 累计访问次数，用于成熟度检测
# mature: 是否已标记为成熟记忆

metadata:
  format_version: 2
  model: temperature
  created: "2026-05-21"

workflow:
  x: 0
  access_count: 42
  mature: true
rust-learning:
  x: 3
  access_count: 8
  mature: false
memory-system:
  x: 0
  access_count: 999
  mature: true
  protected: true
```

**字段说明：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `x` | int | 距离上次访问的天数。0=刚使用，>=48=归档。每日 cron +1。 |
| `access_count` | int | 累计访问次数。每次 read_file 该记忆时 +1。用于成熟度判定。 |
| `mature` | bool | 是否被标记为成熟记忆。标记后会在适当时机转为永久知识。 |
| `protected` | bool (可选) | 保护标记，该记忆永不归档。仅用于 critical 系统文件如 memory-system。 |

#### 2.2 索引条目格式（更新）

保持与旧系统兼容的格式，仅语义变化：

```
# 旧：idx:workflow [act=0] → active/workflow.md
# 新：idx:workflow [x=0] → active/workflow.md
```

`x=N` 表示活跃度天数，比 `act=N` 更准确地反映语义。

#### 2.3 目录结构

```
~/.hermes/memories/
├── ACTIVITY.yaml              ← 温度系统（x, access_count, mature）
├── MEMORY.md                  ← 索引 + 小型事实 (memory tool 管理)
├── USER.md                    ← 用户资料 (不变)
├── active/                    ← 活跃记忆 (y > 0)
│   ├── workflow.md
│   ├── rust-learning.md
│   └── ...
├── archive/
│   ├── forgotten/             ← 遗忘归档（y ≈ 0, 可重新激活）
│   │   └── FORGET_LOG.md
│   └── mature/                ← 成熟归档（永久知识副本）
└── knowledge/                 ← 成熟记忆升级的永久知识文档
    ├── workflow.md            ← 工作流的固化知识
    └── ...
```

### 3. 温度对记忆内容详略的映射规则

这是本设计的核心。温度直接决定记忆文件的内容详细程度。

#### 五级详细度映射

```
TIER_5: y ≥ 40 (x ≤ 1.5天)   — 🔥 爆炸详细
TIER_4: 25 ≤ y < 40 (1.5 < x ≤ 5天) — 📗 详细
TIER_3: 10 ≤ y < 25 (5 < x ≤ 20天)  — 📙 摘要
TIER_2: 2 ≤ y < 10 (20 < x ≤ 42天)  — 📕 极简
TIER_1: y < 2 (x > 42天)     — 📦 归档待命
ARCHIVE: y ≈ 0 (x ≥ 48天)    — 🗄️ 已归档
```

#### 每级的文件内容规范

```
┌─────────────────────────────────────────────────────┐
│ TIER_5 (y ≥ 40) — 爆炸详细                            │
│                                                       │
│ # <topic> — <标题>                                    │
│                                                       │
│ ## 概要                                               │
│ <完整的上下文说明，2-3段>                                │
│                                                       │
│ ## 核心事实                                            │
│ - <关键点1>（含具体数值/路径/命令）                       │
│ - <关键点2>                                            │
│ - ...                                                  │
│                                                       │
│ ## 详细说明                                            │
│ <完整的推理过程、代码示例、配置示例等>                      │
│                                                       │
│ ## 相关链接                                            │
│ - 相关记忆：<topic2>, <topic3>                          │
│ - 文件路径：<path>                                      │
│                                                       │
│ ## 上次使用                                            │
│ <当前时间戳，上下文摘要>                                  │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│ TIER_4 (25 ≤ y < 40) — 详细                            │
│                                                       │
│ # <topic> — <标题>                                    │
│                                                       │
│ ## 核心事实                                            │
│ - <关键点1>（含数值/路径）                                │
│ - <关键点2>                                            │
│ - ...                                                  │
│                                                       │
│ ## 关键细节                                            │
│ <去除示例和推理过程，保留结论和关键配置>                    │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│ TIER_3 (10 ≤ y < 25) — 摘要                            │
│                                                       │
│ # <topic> — <标题>                                    │
│                                                       │
│ - <关键点1>（一句话）                                    │
│ - <关键点2>                                            │
│ - <关键路径/命令>                                       │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│ TIER_2 (2 ≤ y < 10) — 极简                             │
│                                                       │
│ # <topic> — <标题>                                    │
│ <一行概要，最长 150 chars>                              │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│ TIER_1 (y < 2) — 归档待命                               │
│                                                       │
│ 索引中保留 [x=N]，但提示 agent 内容已精简                 │
│ 文件内容同 TIER_2（极简行），等待 cron 触发归档           │
└─────────────────────────────────────────────────────┘
```

#### 温度上升时的内容恢复

当记忆被重新使用（x=0），agent 应重建详细内容：

```
温度回弹流程：
1. x=0 → y→∞ → TIER_5
2. agent 检查当前 active/<topic>.md 内容
3. 如果内容比 TIER_5 更少（例如处于 TIER_3 时期被重新激活）
4. agent 根据对当前对话上下文的理解，补全内容至 TIER_5 规范
5. 写回文件
6. 更新 ACTIVITY.yaml 中该记忆的 x=0, access_count += 1
```

**重建的智能性：**
- agent 不是从存档恢复原始内容（那个已经丢失/精简了）
- 而是基于「当前的知识 + 对话上下文」重建一个当前最相关的详细版本
- 这模拟了人类的回忆过程：不是播放录像带，而是基于线索重建

### 4. 双层归档机制

#### 4.1 遗忘归档（Forgetting Archive）

**触发条件：** `x ≥ 48`（温度 ≈ 0）

```
遗忘归档流程：

cron 触发 or 会话内检查发现 x ≥ 48:
  1. 计算 y = -log₁.₀₉(x) + 45
  2. 确认 y ≈ 0（浮点容差 < 0.5）
  3. 检查 memory-system 保护标记 — 跳过受保护记忆
  4. 检查 mature 标记：
     a. 如果 mature == true → 执行成熟归档流程（见 4.2）
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
     d. memory add "idx:<topic> [x=0] → active/<topic>.md"
     e. 在 ACTIVITY.yaml 中添加条目：<topic>: {x: 0, access_count: <old_count+1>, mature: false}
     f. 在回复中注明：✅ 已重新激活记忆「topic」
```

#### 4.2 成熟归档（Mature Archive）

**触发条件：** 记忆被频繁调用 — 在 temperature 系统中由 `access_count` 和 `x` 的复位频率共同决定。

**成熟度判定算法：**

```
算法：is_mature(topic)

输入：ACTIVITY.yaml 中该记忆的 {x, access_count}
输出：bool

逻辑：
  条件 A（频率触发）：
    最近 30 天内 x 被清零（被使用）≥ 5 次
    └─ 实现：cron 脚本维护一个 use_history 时间戳列表
    └─ 简化实现：access_count 在最近 N 次 cron 运行中增长迅速
    
  条件 B（连续性触发）：
    连续 7 个会话都加载了该记忆
    └─ 实现：记录最近的会话时间戳
    
  条件 C（人工标记）：
    用户或 agent 明确设置 mature: true

  如果 (A 或 B 或 C) 且 mature == false:
    标记 mature = true
    返回 true
  否则返回 false
```

**简化实现（避免维护历史列表）：**

由于维护时间戳列表增加了复杂度，建议使用以下简化方案：

```
简化判定条件（三选一即可标记成熟）：

1. access_count ≥ 20 且 x ≤ 3
   └─ 访问超过 20 次且最近 3 天用过 → 高频使用 → 成熟

2. access_count / (总记忆数 × 30天) > 0.2
   └─ 相对使用频率高于 20% → 成熟

3. 用户或 agent 主动设置 mature: true
   └─ 人工标记 → 成熟
```

**成熟后的处理流程：**

```
成熟归档流程（在遗忘检查时触发）：

当记忆同时满足：
  - mature == true
  - x ≥ 48 (温度≈0，即将被遗忘归档)

说明该记忆虽然高频使用（成熟），但近期未被使用（温度已降）。
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

#### 4.3 永久知识文档格式

```
~/.hermes/knowledge/<topic>.md

# <topic> — <标题>

**来源：** memory-temperature / mature promotion
**固化时间：** 2026-05-21
**原始记忆：** archive/mature/<topic>.md

## 核心知识

<提炼后的永久有效内容，去除时间敏感信息>

## 使用场景

<什么场景下应加载此知识>
```

**knowledge/ 与 active/ 的区别：**
- `active/` 中的文件由温度系统管理，内容随温度变化
- `knowledge/` 中的文件是永久固化知识，不被温度系统管理
- agent 可以随时读取 knowledge/ 中的文件（类似 skill 文档）

### 5. 记忆协议更新（memory-system.md）

需要将新的温度模型协议写入 memory-system.md。

**核心变更：**
- 用温度 y 替代 act，用 x（天数）替代线性计数器
- 加载时根据 y 判断需要读取多少内容
- 温度回弹时需重建详细内容
- 新增成熟记忆检测逻辑
- 新增双层归档逻辑

### 6. Cron 脚本改造

#### 6.1 memory-temperature.py（替代 memory-decay.py）

```python
#!/usr/bin/env python3
"""
Memory Temperature — 每日温度衰减守护
每天凌晨 3 点执行：

1. 读取 ACTIVITY.yaml（v2 格式）
2. 所有记忆 x += 1（受保护记忆除外）
3. 对每个记忆计算 y = -log₁.₀₉(x) + 45
4. 根据 y 值执行对应操作：
   a. y ≥ 40 (TIER_5) — 无需操作
   b. 25 ≤ y < 40 (TIER_4) — 检查是否需要精简至 TIER_4（非强制，交给 agent）
   c. 10 ≤ y < 25 (TIER_3) — 同上
   d. 2 ≤ y < 10 (TIER_2) — 同上
   e. y < 2 (TIER_1) — 标记为待归档
   f. y ≈ 0 (x ≥ 48) — 执行归档
5. 成熟度检测：对 access_count 高的记忆标记 mature
6. 执行归档（遗忘归档 or 成熟归档）
7. 写回 ACTIVITY.yaml
8. 记录事件日志

注意：
- cron 只做 x += 1 和归档操作
- 内容精简（TIER 降级）由 agent 在下次使用时按温度决定
- 温度回弹和内容重建由 agent 在 read_file 时处理
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
BASE = 1.09
ARCHIVE_THRESHOLD_X = 48
MATURE_ACCESS_THRESHOLD = 20
MATURE_X_THRESHOLD = 3

logs = []


def temperature(x: int) -> float:
    """计算温度值"""
    if x == 0:
        return 999.0  # 代表无穷大
    y = -math.log(x, BASE) + 45
    return max(0.0, y)


def parse_activity_v2(text: str) -> dict:
    """解析 v2 格式的 ACTIVITY.yaml"""
    import yaml  # 实际实现应使用 PyYAML
    return yaml.safe_load(text)


def write_activity_v2(data: dict):
    """写入 v2 格式的 ACTIVITY.yaml"""
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


def write_forget_log(topic: str, x: int, y: float, reason: str):
    ARCHIVE_FORGOTTEN_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"| {now} | {topic} | x={x} | y={y:.1f} | {reason} |\n"
    if not FORGET_LOG.exists():
        header = """# Memory Forgetting Log (Temperature Model v2)
| 时间 | 主题 | x值 | 温度 | 原因 |
|------|------|-----|------|------|
"""
        FORGET_LOG.write_text(header + entry, encoding="utf-8")
    else:
        with open(FORGET_LOG, "a", encoding="utf-8") as f:
            f.write(entry)


def forget_archive(topic: str, x: int, y: float, data: dict):
    """遗忘归档：移到 archive/forgotten/"""
    src = ACTIVE_DIR / f"{topic}.md"
    dst = ARCHIVE_FORGOTTEN_DIR / f"{topic}.md"
    if src.exists():
        ARCHIVE_FORGOTTEN_DIR.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    # Remove from MEMORY.md
    entries = read_memory_index()
    new_entries = [e for e in entries if not e.startswith(f"idx:{topic}")]
    write_memory_index(new_entries)
    # Remove from ACTIVITY.yaml
    del data["memories"][topic]
    write_forget_log(topic, x, y, "forgotten")


def mature_archive(topic: str, x: int, y: float, data: dict, memory_info: dict):
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
    # Read original content for knowledge extraction
    original_content = ""
    if src.exists():
        original_content = src.read_text(encoding="utf-8")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    knowledge_content = f"""# {topic}

**来源：** memory-temperature mature promotion
**固化时间：** {now}
**访问次数：** {memory_info.get('access_count', 0)}
**原始存档：** archive/mature/{topic}.md

**注意：** 此文件由温度系统自动生成，内容为原始记忆的副本。
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
    write_forget_log(topic, x, y, "mature archived")


def main():
    run_time = datetime.now()
    logs.append(f"=== Memory Temperature Run: {run_time} ===")
    
    if not ACTIVITY_FILE.exists():
        logs.append("❌ ACTIVITY.yaml not found, aborting")
        return
    
    data = parse_activity_v2(ACTIVITY_FILE.read_text(encoding="utf-8"))
    memories = data.get("memories", {})
    
    if not memories:
        logs.append("ℹ️  No memory entries in ACTIVITY.yaml")
        return
    
    logs.append(f"📊  Loaded {len(memories)} memories")
    
    to_forget = []    # (topic, x, y)
    to_mature = []    # (topic, x, y, info)
    
    for topic, info in memories.items():
        if info.get("protected", False):
            logs.append(f"🛡️  {topic}: protected, skipping")
            continue
        
        x = info.get("x", 0) + 1  # 每天 +1
        info["x"] = x
        y = temperature(x)
        
        # 成熟度检测
        if not info.get("mature", False):
            access_count = info.get("access_count", 0)
            if access_count >= MATURE_ACCESS_THRESHOLD and x <= MATURE_X_THRESHOLD:
                info["mature"] = True
                logs.append(f"🌟  {topic}: matured (access_count={access_count}, x={x})")
        
        # 归档判定
        if x >= ARCHIVE_THRESHOLD_X:
            if info.get("mature", False):
                to_mature.append((topic, x, y, info))
            else:
                to_forget.append((topic, x, y))
        
        logs.append(f"📈  {topic}: x={x}, y={y:.1f}" + 
                     (" 🎓 mature" if info.get("mature") else "") +
                     (" 🔜 archive" if x >= ARCHIVE_THRESHOLD_X else ""))
    
    # 执行归档
    for topic, x, y in to_forget:
        forget_archive(topic, x, y, data)
        logs.append(f"📦  {topic}: forgotten archived")
    
    for topic, x, y, info in to_mature:
        mature_archive(topic, x, y, data, info)
        logs.append(f"🎓  {topic}: mature archived")
    
    # 写回
    write_activity_v2(data)
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

更新月度回顾脚本以使用温度指标替代线性活跃度指标。

**变更点：**
- 报告每个记忆的温度值 y（而非 act）
- 标注 TIER 等级
- 统计归档原因分布（遗忘 vs 成熟）
- 报告 knowledge/ 中的固化知识数量

### 7. 与 Hermes 原生 memory tool 的共存策略

#### 7.1 索引格式兼容性

旧索引格式（当前 MEMORY.md 中的）：
```
idx:workflow [act=N] → active/workflow.md
```

新索引格式：
```
idx:workflow [x=N] → active/workflow.md
```

**迁移期间：** 两种格式可以共存。agent 统一解析 `idx:topic [...] → path`，忽略括号内的具体标记名。cron 脚本也会兼容两种格式。

#### 7.2 温度模型 vs memory tool 的关系

```
memory tool（system prompt 注入）  →  MEMORY.md（索引 + 小型事实）
                                          ↑
温度系统（独立层）                    →  ACTIVITY.yaml（x, access_count, mature）
                                          ↓
active/*.md（内容由温度决定详略）      →  agent 按需 read_file
```

- memory tool 仍然管理 MEMORY.md 的读写（add/remove/replace）
- 温度系统通过 ACTIVITY.yaml 独立管理活跃度
- 两者的接口是 MEMORY.md 中的 `idx:topic [x=N]` 条目
- memory tool 的 `memory("replace")` 不应修改 ACTIVITY.yaml
- cron 脚本修改 ACTIVITY.yaml 后，下一次 memory tool 读取时自动同步（冻结快照模式）

#### 7.3 与 memory tool 的读写分离

```
┌────────────────────────────────────────────────────┐
│ 写操作（温度系统控制）                                │
├────────────────────────────────────────────────────┤
│ memory tool 写 MEMORY.md（add/remove/replace）      │
│ cron 脚本写 ACTIVITY.yaml（x += 1, 归档）           │
│ agent 写 active/*.md（内容详略调整）                  │
│ agent 写 ACTIVITY.yaml（x=0 回弹, access_count++）  │
└────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────┐
│ 读操作（按需加载）                                    │
├────────────────────────────────────────────────────┤
│ system prompt 读取 MEMORY.md（冻结快照）              │
│ agent 解析 idx:topic [x=N]                          │
│ agent 根据 x 计算 y = -log₁.₀₉(x) + 45             │
│ agent 根据 y 决定读取深度：                            │
│   y ≥ 40 → 完整读取                                  │
│   10 ≤ y < 40 → 选择性读取关键部分                      │
│   y < 10 → 只读第一行                                 │
└────────────────────────────────────────────────────┘
```

### 8. 迁移方案

从线性活跃度模型（ADR-001）到温度模型（ADR-002）的迁移步骤。

#### Phase 0: 准备（预迁移）

```bash
# 1. 备份当前系统
cp ~/.hermes/memories/ACTIVITY.yaml ~/.hermes/memories/ACTIVITY.yaml.bak
cp ~/.hermes/memories/MEMORY.md ~/.hermes/memories/MEMORY.md.bak

# 2. 创建新目录结构
mkdir -p ~/.hermes/memories/archive/forgotten
mkdir -p ~/.hermes/memories/archive/mature
mkdir -p ~/.hermes/knowledge

# 3. 如果 archive/ 中有旧归档文件，移到 forgotten/ 子目录
if [ -d ~/.hermes/memories/archive ] && [ ! -d ~/.hermes/memories/archive/forgotten ]; then
  for f in ~/.hermes/memories/archive/*.md; do
    [ -f "$f" ] && mv "$f" ~/.hermes/memories/archive/forgotten/
  done
fi
```

#### Phase 1: 数据格式迁移（自动脚本）

执行迁移转换脚本，将 ACTIVITY.yaml 从 v1 格式转换为 v2 格式：

```python
# 迁移逻辑：
# v1: topic: act (int)
# v2: topic: {x: act, access_count: act, mature: false}

def migrate_v1_to_v2(old_yaml: dict) -> dict:
    return {
        "metadata": {
            "format_version": 2,
            "model": "temperature",
            "created": datetime.now().strftime("%Y-%m-%d"),
        },
        "memories": {
            topic: {
                "x": act,
                "access_count": 0,  # 开始追踪
                "mature": False,
            }
            for topic, act in old_yaml.items()
        }
    }
```

**注意事项：**
- memory-system 条目应自动添加 `protected: true`
- 旧 act 值直接映射为新 x 值（语义一致：天数）
- access_count 从 0 开始（新指标，旧数据无法追溯）

#### Phase 2: 索引格式更新

将 MEMORY.md 中的 `[act=N]` 替换为 `[x=N]`：

```
# 替换前：idx:workflow [act=0] → active/workflow.md
# 替换后：idx:workflow [x=0] → active/workflow.md
```

纯文本替换，不影响其他内容。如果无需统一，两种格式可以共存。

#### Phase 3: 脚本替换

```bash
# 删除旧脚本
rm ~/.hermes/scripts/memory-decay.py

# 安装新脚本
# 将 memory-temperature.py 写入 ~/.hermes/scripts/

# 更新 crontab
# 旧：0 3 * * * cd ~/.hermes && python3 scripts/memory-decay.py
# 新：0 3 * * * cd ~/.hermes && python3 scripts/memory-temperature.py
```

#### Phase 4: 记忆系统协议更新

更新 ~/.hermes/memories/active/memory-system.md，加入温度模型协议。

#### Phase 5: 回滚方案

如果新系统出现问题：

```bash
# 恢复备份
cp ~/.hermes/memories/ACTIVITY.yaml.bak ~/.hermes/memories/ACTIVITY.yaml
cp ~/.hermes/memories/MEMORY.md.bak ~/.hermes/memories/MEMORY.md

# 恢复旧脚本
# 将 memory-decay.py 放回 ~/.hermes/scripts/
# 恢复 crontab

# 整理目录（可选）
rm -rf ~/.hermes/memories/archive/forgotten
rm -rf ~/.hermes/memories/archive/mature
```

---

## 替代方案

### 未采纳：指数衰减模型

```
y = 45 * e^(-λx)
```

**理由：** 指数衰减在 x=[0,5] 下降太快，在 x=[10,+] 下降太慢。对数衰减（本方案）在短期快速衰减后趋于平缓，更符合人类记忆的艾宾浩斯遗忘曲线。

### 未采纳：S 形曲线（Sigmoid）

```
y = 45 / (1 + e^(k(x - x₀)))
```

**理由：** Sigmoid 在阈值附近变化剧烈，会产生「悬崖效应」，同线性模型的阈值悬崖问题。对数衰减是平滑的、逐级的。

### 未采纳：分段线性（3段）

```
y = 45 - 5x      (0 ≤ x ≤ 7)
y = 10 - 0.3(x-7) (7 < x ≤ 30)
y = 3 - 0.1(x-30) (30 < x ≤ 48)
```

**理由：** 分段线性虽然简单，但不够优雅。对数公式用单一数学表达式描述了整个衰减过程，没有断点，更便于理解和计算。

### 未采纳：双层存储（完整版 + 摘要版同时保留）

**理由：** 磁盘空间不是瓶颈，不需要同时保留两个版本。温度升高时 agent 可以重建内容，降低时精简即可。

---

## 影响分析

### 正面影响

| 方面 | 旧系统（线性） | 新系统（温度） |
|------|---------------|---------------|
| 记忆详细度 | 全有或全无 | 五级梯度，按需分配 |
| 遗忘曲线 | 线性（不符合人类认知） | 对数（符合艾宾浩斯曲线） |
| 归档阈值 | 30 天突发遗忘 | 48 天渐进遗忘 |
| 内容压缩 | 无 | 温度越低越精简 |
| 高频记忆 | 同低频一样被遗忘 | 成熟标记，固化保存 |
| 知识沉淀 | 无 | mature → knowledge/ 固化 |
| prompt 效率 | 所有记忆等长注入 | 温度越低越短，节省空间 |

### 负面影响

| 风险 | 缓解措施 |
|------|---------|
| 温度回弹时内容重建可能丢失细节 | agent 在重建时利用当前对话上下文补全，非恢复丢失内容 |
| 新增 access_count 字段需从0开始 | 成熟度判定在初期不会触发，给了足够的冷启动时间 |
| 归档目录变为两层（forgotten/ + mature/） | 脚本自动处理，对 agent 透明 |
| knowledge/ 目录初始为空 | 成熟归档自然填充，不需要预填 |
| YAML 格式从扁平变嵌套 | PyYAML 读写一致，python 脚本兼容 |

### 对现有记忆的影响

- 所有现有记忆的 act 值直接映射为 x（语义一致）
- access_count 初始为 0
- mature 初始为 false
- 受保护记忆（memory-system）手动添加 `protected: true`

### 向后兼容性

- MEMORY.md 中的索引条目格式：`[act=N]` 和 `[x=N]` 均可解析
- cron 脚本完全替换，不保留旧版
- agent 协议更新为新版，旧版协议不再使用

---

## 实现计划

### Phase 0: 准备（30分钟）

- [ ] 备份当前 ACTIVITY.yaml 和 MEMORY.md
- [ ] 创建 archive/forgotten/、archive/mature/、knowledge/ 目录
- [ ] 移动现有 archive/ 内容到 archive/forgotten/

### Phase 1: 数据迁移（1小时）

- [ ] 创建 ACTIVITY.yaml 从 v1 到 v2 的迁移脚本
- [ ] 执行迁移，验证 YAML 格式正确
- [ ] 更新 MEMORY.md 中索引条目标记（[act=N] → [x=N]，可选）
- [ ] 验证 memory tool 可正常读取新格式

### Phase 2: Cron 脚本实现（2小时）

- [ ] 编写 memory-temperature.py（完整实现，含 YAML 读写、温度计算、归档逻辑）
- [ ] 编写单元测试（温度计算、归档判定、成熟度判定）
- [ ] 手动运行测试，验证输出
- [ ] 更新 crontab

### Phase 3: 记忆系统协议更新（1小时）

- [ ] 重写 memory-system.md → 温度模型协议
- [ ] 写入 ADR-002 设计文档链接
- [ ] 将 memory-system 标记为 protected: true

### Phase 4: Agent 协议集成（30分钟）

- [ ] 添加温度计算公式到 agent 的系统提示或 SOUL
- [ ] 添加 TIER 映射表
- [ ] 添加温度回弹时的内容重建指引
- [ ] 添加成熟记忆检测指引

### Phase 5: 月度回顾脚本更新（30分钟）

- [ ] 更新 memory-monthly-review.py 使用温度指标
- [ ] 添加 mature/knowledge 统计

### Phase 6: 监控与调优（持续）

- [ ] 监控归档频率是否合理
- [ ] 根据实际使用调整成熟度判定参数
- [ ] 收集 agent 反馈，优化 TIER 映射规则
- [ ] 如果 knowledge/ 中文件增长过快，考虑引入 knowledge/ 的索引机制

---

## 附录 A：温度计算速查表

```python
import math
BASE = 1.09

def temperature(x):
    if x == 0:
        return float('inf')
    return -math.log(x, BASE) + 45

# 常用值
for x in [0, 1, 3, 5, 7, 10, 14, 21, 30, 40, 45, 48]:
    y = temperature(x) if x > 0 else float('inf')
    if y == float('inf'):
        print(f"x={x:3d} → y=∞   (TIER_5 🔥)")
    else:
        tier = "TIER_5 🔥" if y >= 40 else \
               "TIER_4 📗" if y >= 25 else \
               "TIER_3 📙" if y >= 10 else \
               "TIER_2 📕" if y >= 2 else \
               "ARCHIVE 🗄️"
        print(f"x={x:3d} → y={y:6.1f} ({tier})")
```

输出：
```
x=  0 → y=∞    (TIER_5 🔥)
x=  1 → y= 45.0 (TIER_5 🔥)
x=  3 → y= 32.3 (TIER_4 📗)
x=  5 → y= 26.3 (TIER_4 📗)
x=  7 → y= 22.4 (TIER_3 📙)
x= 10 → y= 18.3 (TIER_3 📙)
x= 14 → y= 14.4 (TIER_3 📙)
x= 21 → y=  9.7 (TIER_2 📕)
x= 30 → y=  5.5 (TIER_2 📕)
x= 40 → y=  2.2 (TIER_2 📕)
x= 45 → y=  0.8 (ARCHIVE 🗄️)
x= 48 → y=  0.1 (ARCHIVE 🗄️)
```

## 附录 B：与旧系统的对比速查

| 概念 | 旧系统（线性） | 新系统（温度） |
|------|---------------|---------------|
| 活跃度指标 | act（线性计数器） | x（活跃天数） |
| 详细程度 | 全有或全无 | 五级 TIER（由 y 决定） |
| 遗忘驱动力 | act += 1（每日） | x += 1（每日） |
| 使用回弹 | act = 0 | x = 0, access_count += 1 |
| 归档阈值 | act > 30 | x ≥ 48 |
| 归档类型 | 单一遗忘归档 | 遗忘归档 + 成熟归档 |
| 成熟度 | 无 | access_count ≥ 20 且 x ≤ 3 |
| 知识固化 | 无 | mature → knowledge/ |
| 受保护记忆 | 代码硬编码 | `protected: true` YAML 标记 |
| 日期记录 | 无 | updated_at 元数据 |
