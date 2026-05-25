#!/usr/bin/env python3
"""
enrichment.py — 记忆降级与丰富基础设施

提供 TIER 驱动的磁盘物理降级（degrade）和对话上下文追加（enrich）。
"""

import json
import re
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
    from curve_memory.core.activity import parse_timestamp
    t_days = (now - parse_timestamp(info.get("t", 0))) / 86400
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
            from curve_memory.core.activity import format_timestamp
            memories[topic]["t"] = format_timestamp()
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

# === 记忆文件格式解析 ===
# 格式:
#   ## topic-name
#   **Summary**: <agent 维护的单行摘要>
#
#   **Details**:
#   <详细信息>
#
#   ## Enriched (conversation)
#   <时间戳>
#   <追加内容>
#
#   note: xxx

SUMMARY_RE = re.compile(r'^\*\*Summary\*\*:\s*(.*)', re.MULTILINE)
DETAILS_HEADER_RE = re.compile(r'^\*\*Details\*\*:', re.MULTILINE)


def _parse_memory(content: str) -> dict:
    """解析记忆文件为结构化 dict

    返回:
        {
            "topic": str,
            "summary": str or "",
            "details": str or "",
            "enriched": str,  # everything from ## Enriched onwards
            "note_refs": [str],
        }
    """
    result = {
        "topic": "",
        "summary": "",
        "details": "",
        "enriched": "",
        "note_refs": [],
    }

    # Extract topic name from ## heading
    m = re.search(r'^##\s+(\S+)', content)
    if m:
        result["topic"] = m.group(1)

    # Extract summary
    m = SUMMARY_RE.search(content)
    if m:
        result["summary"] = m.group(1).strip()

    # Split into header and enriched sections
    parts = re.split(r'^##\s+Enriched\b', content, maxsplit=1, flags=re.MULTILINE)
    header = parts[0]
    result["enriched"] = ("## Enriched" + parts[1]) if len(parts) > 1 else ""

    # Extract details from header
    if DETAILS_HEADER_RE.search(header):
        # Has **Details**: section — extract content after it
        d_parts = DETAILS_HEADER_RE.split(header, maxsplit=1)
        result["details"] = d_parts[1].strip() if len(d_parts) > 1 else ""
        # Summary is before the details header
        summary_part = d_parts[0]
        m = SUMMARY_RE.search(summary_part)
        if m:
            result["summary"] = m.group(1).strip()
    else:
        # No details section — everything after summary is flat content
        pass

    # Extract note refs from full content
    from curve_memory.core.note import extract_note_refs
    result["note_refs"] = extract_note_refs(content)

    return result


def _build_memory(topic: str, summary: str = "", details: str = "",
                  enriched: str = "", note_refs: list[str] = None) -> str:
    """从结构组件重建记忆文件"""
    # Strip note: lines from enriched content (they'll be appended via note_refs)
    if enriched:
        enriched_lines = [l for l in enriched.split("\n") if not l.strip().startswith("note:")]
        enriched = "\n".join(enriched_lines)

    lines = [f"## {topic}"]
    if summary:
        lines.append(f"**Summary**: {summary}")
    if details:
        lines.append("")
        lines.append("**Details**:")
        lines.append(details)
    if enriched:
        lines.append("")
        lines.append(enriched)
    if note_refs:
        for ref in note_refs:
            lines.append(f"note: {ref}")
    return "\n".join(lines) + "\n"

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
    """检查内容长度是否 <= 目标 TIER 大小（只读检查，不截断）"""
    target = _target_size(tier_level)
    return len(content) <= target


def degrade_memory(topic: str, memories_dir: Path) -> bool:
    """将单个记忆标记为待语义降级（daytime 行为）

    不再截断文件。改为在 ACTIVITY.yaml 中设置 pending_summary: true，
    由凌晨的 semantic degrade cron 命令统一处理。

    返回值：True = 已标记 pending，False = 无需处理
    """
    try:
        data = _read_activity(memories_dir)
        memories = data.get("memories", {})
        if topic not in memories:
            logger.debug("degrade: topic '%s' not in ACTIVITY.yaml", topic)
            return False

        # 如果已标记，跳过
        if memories[topic].get("pending_summary", False):
            return False

        now = time.time()
        r = _r_for_topic(topic, data, now)
        tier_level = r_to_tier_level(r)

        mem_path = memories_dir / "active" / f"{topic}.md"
        if not mem_path.exists():
            logger.debug("degrade: file not found at %s", mem_path)
            return False

        content = mem_path.read_text(encoding="utf-8")

        # 如果内容已经在目标大小内，跳过（无需降级）
        if content_size_fit(content, tier_level):
            return False

        # 标记为待语义降级（不截断文件）
        memories[topic]["pending_summary"] = True
        _write_activity(memories_dir, data)
        logger.debug("flagged '%s' for semantic degradation (TIER_%d, %d > %d chars)",
                     topic, tier_level, len(content), _target_size(tier_level))
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
    summary: Optional[str] = None,
) -> bool:
    """追加新信息到记忆文件（新格式：**Summary** + **Details**）

    summary 参数由主 Agent 提供，每次写入时更新。
    new_content 追加到 **Details** 部分。

    1. 读取 active/{topic}.md（不存在则按新格式创建）
    2. 更新 **Summary**（如果提供了 summary）
    3. 去重检查，追加 new_content 到 **Details**
    4. 写回文件并更新活性
    """
    if not new_content or not new_content.strip():
        if summary is None:
            return False

    try:
        active_dir = memories_dir / "active"
        active_dir.mkdir(parents=True, exist_ok=True)
        mem_path = active_dir / f"{topic}.md"

        # 读取/初始化内容
        if mem_path.exists():
            existing = mem_path.read_text(encoding="utf-8")
            parsed = _parse_memory(existing)
        else:
            parsed = {"topic": topic, "summary": "", "details": "",
                      "enriched": "", "note_refs": []}

        # 更新摘要
        if summary is not None and summary.strip():
            parsed["summary"] = summary.strip()

        # 追加新内容到 Details
        if new_content and new_content.strip():
            new_text = new_content.strip()
            existing_lower = (parsed["details"] + "\n" + parsed["enriched"]).lower()

            # 去重：跳过已是子串或已存在的行
            if new_text.lower() in existing_lower:
                logger.debug("enrich: new_content is a substring of existing, skipping")
            else:
                existing_lines = set((parsed["details"] + "\n" + parsed["enriched"]).splitlines())
                new_lines = []
                for line in new_text.splitlines():
                    if line.strip() and line.strip() not in existing_lines:
                        new_lines.append(line)
                if new_lines:
                    if parsed["details"]:
                        parsed["details"] += "\n" + "\n".join(new_lines)
                    else:
                        parsed["details"] = "\n".join(new_lines)
                    logger.debug("enriched '%s' from %s (%d new lines)", topic, source, len(new_lines))

        # 重建文件
        final_content = _build_memory(
            topic=topic,
            summary=parsed["summary"],
            details=parsed["details"],
            enriched=parsed["enriched"],
            note_refs=parsed["note_refs"],
        )
        mem_path.write_text(final_content, encoding="utf-8")

        # 更新活性
        _touch_memory_local(topic, memories_dir)
        return True

    except Exception as e:
        logger.debug("enrich_memory error for '%s': %s", topic, e)
        return False


# ── Embedding index sweep ──────────────────────────────────────


def _needs_reindex(mem_path: Path, index_path: Path) -> bool:
    """检查是否需要重新计算 embedding 索引"""
    if not index_path.exists():
        return True
    # 如果内容比索引新，需要更新
    md_mtime = mem_path.stat().st_mtime
    idx_mtime = index_path.stat().st_mtime
    return md_mtime > idx_mtime + 1  # 1秒容差


def index_sweep(memories_dir: Path, embedder) -> dict:
    """扫描所有活跃记忆，重建缺失或过期的 embedding 索引。

    返回值：
        {
            "indexed": int,     # 重建的主题数
            "cleaned": int,     # 清理的孤立索引文件数
            "errors": int,      # 处理失败数
            "details": [        # 每个主题的处理记录
                {"topic": str, "status": "ok"|"skipped"|"error",
                 "chunks": int|None, "message": str|None}
            ]
        }
    """
    result = {
        "indexed": 0,
        "cleaned": 0,
        "errors": 0,
        "details": [],
    }

    active_dir = memories_dir / "active"
    embedding_dir = memories_dir / ".embedding_index"
    if not active_dir.exists():
        return result

    embedding_dir.mkdir(parents=True, exist_ok=True)

    # 获取活跃主题列表
    data = _read_activity(memories_dir)
    active_topics = set(data.get("memories", {}).keys())

    # 1. 对每个活跃主题检查索引
    for topic in sorted(active_topics):
        mem_path = active_dir / f"{topic}.md"
        if not mem_path.exists():
            result["details"].append({
                "topic": topic, "status": "skipped",
                "message": "no .md file in active/",
            })
            continue

        index_path = embedding_dir / f"{topic}.jsonl"

        if not _needs_reindex(mem_path, index_path):
            result["details"].append({
                "topic": topic, "status": "skipped",
                "message": "up to date",
            })
            continue

        # 读取内容
        try:
            content = mem_path.read_text(encoding="utf-8")
        except Exception as e:
            result["errors"] += 1
            result["details"].append({
                "topic": topic, "status": "error",
                "message": f"read failed: {e}",
            })
            continue

        if not content.strip():
            result["details"].append({
                "topic": topic, "status": "skipped",
                "message": "empty content",
            })
            continue

        # 分块（按行分组，每块不超过 2000 字符）
        lines = content.splitlines()
        chunks = []
        current_chunk = []
        current_len = 0
        for line in lines:
            current_chunk.append(line)
            current_len += len(line) + 1  # +1 for newline
            if current_len >= 2000:
                chunks.append("\n".join(current_chunk))
                current_chunk = []
                current_len = 0
        if current_chunk:
            chunks.append("\n".join(current_chunk))

        if not chunks:
            chunks = [content[:2000]]

        # 计算 embedding 并写入
        try:
            with open(index_path, "w", encoding="utf-8") as f:
                for seq, chunk_text in enumerate(chunks):
                    vector = embedder.embed(chunk_text)
                    record = {
                        "topic": topic,
                        "chunk": seq,
                        "text": chunk_text,
                        "vector": vector,
                    }
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
            # 写入成功后才计入
            if index_path.stat().st_size > 0:
                result["indexed"] += 1
                result["details"].append({
                    "topic": topic, "status": "ok",
                    "chunks": len(chunks),
                })
            else:
                index_path.unlink(missing_ok=True)
                result["errors"] += 1
                result["details"].append({
                    "topic": topic, "status": "error",
                    "message": "embed produced empty file",
                })
        except Exception as e:
            # 清理空文件
            if index_path.exists() and index_path.stat().st_size == 0:
                index_path.unlink(missing_ok=True)
            result["errors"] += 1
            result["details"].append({
                "topic": topic, "status": "error",
                "message": f"embed failed: {e}",
            })

    # 2. 清理孤立索引文件（主题已不在活跃列表中的）
    if embedding_dir.exists():
        for fpath in embedding_dir.glob("*.jsonl"):
            if fpath.stem not in active_topics:
                try:
                    fpath.unlink()
                    result["cleaned"] += 1
                except Exception:
                    pass

    return result
