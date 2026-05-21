#!/usr/bin/env python3
"""
enrichment.py — 记忆降级与丰富基础设施

提供 TIER 驱动的磁盘物理降级（degrade）和对话上下文追加（enrich）。
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from curve_memory.core.activity import load_activity, format_activity, parse_activity
from curve_memory.core.tier import forgetting_curve, r_to_tier_level

logger = logging.getLogger(__name__)

# === TIER 内容大小目标 ===
TIER_SIZE_LIMITS = {
    5: 4000,
    4: 2000,
    3: 800,
    2: 300,
    1: 100,
}


# ── Internal helpers ────────────────────────────────────────────────

def _read_activity(memories_dir: Path) -> dict:
    """加载 ACTIVITY.yaml，返回完整 dict"""
    try:
        return load_activity(memories_dir) or {"metadata": {}, "memories": {}}
    except Exception as e:
        logger.debug("read activity error: %s", e)
        return {"metadata": {}, "memories": {}}


def _write_activity(memories_dir: Path, data: dict):
    """写入 ACTIVITY.yaml"""
    try:
        path = memories_dir / "ACTIVITY.yaml"
        path.write_text(format_activity(data), encoding="utf-8")
    except Exception as e:
        logger.debug("write activity error: %s", e)


def _r_for_topic(topic: str, data: dict, now: float) -> float:
    """计算某个主题当前的 R(t) 值"""
    info = data.get("memories", {}).get(topic, {})
    raw_t = info.get("t", 0)
    if isinstance(raw_t, (int, float)) and raw_t > 1000000000:
        t_days = (now - raw_t) / 86400
    else:
        t_days = raw_t
    return forgetting_curve(t_days)


def _target_size(tier_level: int) -> int:
    """TIER 等级 → 最大字符数"""
    return TIER_SIZE_LIMITS.get(tier_level, 4000)


def _condense_content(content: str, tier_level: int) -> str:
    """根据 TIER 级别压缩内容"""
    if not content or not content.strip():
        return ""

    lines = content.splitlines()

    if tier_level >= 5:
        # TIER_5: 截断到 4000 字符
        return content[:4000]

    elif tier_level == 4:
        # TIER_4: 保留前 2000 字符
        return content[:2000]

    elif tier_level == 3:
        # TIER_3: 提取前 5 个有意义的行
        meaningful = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("---"):
                meaningful.append(line)
                if len(meaningful) >= 5:
                    break
        if not meaningful:
            # Fallback: just take first 800 chars
            return content[:800]
        return "\n".join(meaningful)

    elif tier_level == 2:
        # TIER_2: 提取第一个有意义的行（标题/关键点）
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("---"):
                # 限制到 300 字符
                return line[:300]
        # Fallback
        return content[:300]

    elif tier_level == 1:
        # TIER_1: 提取主题名 + 第一句片段
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("---"):
                # 取第一句（按句号/换行分割）
                sentence = stripped.split("。")[0] if "。" in stripped else stripped.split(".")[0]
                return stripped[:100]
        return content[:100]

    # 默认: 截断到 4000
    return content[:4000]


def _touch_memory_local(topic: str, memories_dir: Path):
    """更新记忆的访问时间（本地版，避免循环导入）"""
    try:
        data = _read_activity(memories_dir)
        memories = data.get("memories", {})
        if topic in memories:
            memories[topic]["t"] = int(time.time())
            memories[topic]["access_count"] = memories[topic].get("access_count", 0) + 1
            _write_activity(memories_dir, data)
    except Exception as e:
        logger.debug("local touch error: %s", e)


# ── Tier cache ─────────────────────────────────────────────────────

def _tier_cache_path(memories_dir: Path) -> Path:
    return memories_dir / ".tier_cache.json"


def _load_tier_cache(memories_dir: Path) -> dict:
    """加载 .tier_cache.json"""
    path = _tier_cache_path(memories_dir)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug("tier cache load error: %s", e)
    return {"updated_at": 0, "tiers": {}}


def _save_tier_cache(memories_dir: Path, data: dict):
    """写入 .tier_cache.json"""
    try:
        path = _tier_cache_path(memories_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception as e:
        logger.debug("tier cache save error: %s", e)


# ── Public API ──────────────────────────────────────────────────────

def get_tier_for_topic(topic: str, memories_dir: Path) -> int:
    """读取 ACTIVITY.yaml，返回指定主题的数值 TIER 等级"""
    try:
        data = _read_activity(memories_dir)
        now = time.time()
        r = _r_for_topic(topic, data, now)
        return r_to_tier_level(r)
    except Exception as e:
        logger.debug("get_tier_for_topic error: %s", e)
        return 0


def content_size_fit(content: str, tier_level: int) -> bool:
    """检查内容长度是否 <= 目标 TIER 大小"""
    target = _target_size(tier_level)
    return len(content) <= target


def degrade_memory(topic: str, memories_dir: Path) -> bool:
    """将单个记忆文件降级到当前 TIER 级别应有的内容大小

    1. 计算 R(t) 和 tier_level
    2. 如果内容已在目标大小内 → 返回 False
    3. 压缩并写回, 返回 True
    """
    try:
        data = _read_activity(memories_dir)
        memories = data.get("memories", {})
        if topic not in memories:
            logger.debug("degrade: topic '%s' not in ACTIVITY.yaml", topic)
            return False

        now = time.time()
        r = _r_for_topic(topic, data, now)
        tier_level = r_to_tier_level(r)

        mem_path = memories_dir / "active" / f"{topic}.md"
        if not mem_path.exists():
            logger.debug("degrade: file not found at %s", mem_path)
            return False

        content = mem_path.read_text(encoding="utf-8")

        # 如果内容已经在目标大小内，跳过
        if content_size_fit(content, tier_level):
            return False

        # 降级
        condensed = _condense_content(content, tier_level)
        mem_path.write_text(condensed, encoding="utf-8")
        logger.debug("degraded '%s' to TIER_%d (%d chars)", topic, tier_level, len(condensed))
        return True

    except Exception as e:
        logger.debug("degrade_memory error for '%s': %s", topic, e)
        return False


def degradation_sweep(memories_dir: Path) -> list[str]:
    """扫描所有记忆，将超过 TIER 大小限制的内容降级"""
    degraded = []
    try:
        data = _read_activity(memories_dir)
        memories = data.get("memories", {})
        if not memories:
            return degraded

        now = time.time()
        for topic in memories:
            if degrade_memory(topic, memories_dir):
                degraded.append(topic)
    except Exception as e:
        logger.debug("degradation_sweep error: %s", e)

    return degraded


def detect_tier_drops(memories_dir: Path) -> list[tuple[str, int, int]]:
    """检测所有记忆的 TIER 向下穿越

    维护 .tier_cache.json，比较当前 TIER 与缓存的 TIER，
    返回 (topic, old_tier, new_tier) 列表。
    """
    drops = []
    try:
        data = _read_activity(memories_dir)
        memories = data.get("memories", {})
        if not memories:
            return drops

        cache = _load_tier_cache(memories_dir)
        cached_tiers = cache.get("tiers", {})
        now = time.time()

        current_tiers = {}
        for topic in memories:
            r = _r_for_topic(topic, data, now)
            current_tiers[topic] = r_to_tier_level(r)

            old_tier = cached_tiers.get(topic, current_tiers[topic])
            new_tier = current_tiers[topic]
            if new_tier < old_tier:
                drops.append((topic, old_tier, new_tier))

        # 更新缓存
        cache["tiers"] = current_tiers
        cache["updated_at"] = int(now)
        _save_tier_cache(memories_dir, cache)

    except Exception as e:
        logger.debug("detect_tier_drops error: %s", e)

    return drops


def enrich_memory(
    topic: str,
    new_content: str,
    memories_dir: Path,
    source: str = "conversation",
) -> bool:
    """追加新信息到记忆文件

    1. 读取 active/{topic}.md（不存在则创建）
    2. 去重检查：模糊子串匹配 + 精确行匹配
    3. 追加带时间戳的新 section
    4. 写回文件并更新活性
    """
    if not new_content or not new_content.strip():
        return False

    try:
        active_dir = memories_dir / "active"
        active_dir.mkdir(parents=True, exist_ok=True)
        mem_path = active_dir / f"{topic}.md"

        # 读取/初始化内容
        if mem_path.exists():
            existing = mem_path.read_text(encoding="utf-8")
        else:
            existing = f"# {topic}\n\n"

        # 去重检测
        existing_lower = existing.lower()
        new_lower = new_content.lower().strip()

        # 模糊子串检查
        if new_lower in existing_lower:
            logger.debug("enrich: new_content is a substring of existing content, skipping")
            return False

        # 精确行匹配：只追加新行
        existing_lines = set(existing.splitlines())
        new_lines = []
        for line in new_content.splitlines():
            if line.strip() and line.strip() not in existing_lines:
                new_lines.append(line)

        if not new_lines:
            logger.debug("enrich: all lines already present, skipping")
            return False

        # 构建追加内容
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        enriched_section = (
            f"\n\n## Enriched ({source})\n"
            f"{now_str}\n"
            f"{chr(10).join(new_lines)}"
        )

        # 写回
        final_content = existing.rstrip("\n") + enriched_section + "\n"
        mem_path.write_text(final_content, encoding="utf-8")
        logger.debug("enriched '%s' from %s (%d new lines)", topic, source, len(new_lines))

        # 更新活性（相当于 _touch_memory）
        _touch_memory_local(topic, memories_dir)
        return True

    except Exception as e:
        logger.debug("enrich_memory error for '%s': %s", topic, e)
        return False
