#!/usr/bin/env python3
"""
chunker.py — Markdown H2 章节分割

按 ## (H2) 标题将 markdown 文件分割为 chunk。
每个 chunk 上限 2000 tokens (~8000 chars)。
"""

import re
from pathlib import Path
from typing import List, Dict

# H2 标题正则
H2_PATTERN = re.compile(r'^##\s+(.+)$', re.MULTILINE)
MAX_CHUNK_CHARS = 8000


def chunk_markdown(topic: str, content: str) -> List[Dict]:
    """
    将 markdown 内容按 H2 分割为 chunk。

    返回:
    [{"topic": str, "chunk": str (标题), "text": str (内容), "mtime": str}]
    """
    if not content.strip():
        return []

    # 找到所有 H2 标题的位置
    matches = list(H2_PATTERN.finditer(content))
    chunks = []

    if not matches:
        # 没有 H2，整个文件作为一个 chunk
        text = content.strip()
        if len(text) > MAX_CHUNK_CHARS:
            text = text[:MAX_CHUNK_CHARS] + "..."
        chunks.append({
            "topic": topic,
            "chunk": "概述",
            "text": text,
        })
        return chunks

    for i, m in enumerate(matches):
        title = m.group(1).strip()
        start = m.start()
        # 下一个 H2 或文件结尾
        if i + 1 < len(matches):
            end = matches[i + 1].start()
        else:
            end = len(content)

        text = content[start:end].strip()
        if len(text) > MAX_CHUNK_CHARS:
            text = text[:MAX_CHUNK_CHARS] + "..."

        chunks.append({
            "topic": topic,
            "chunk": title,
            "text": text,
        })

    return chunks


def chunk_file(filepath: Path, topic: str = None) -> List[Dict]:
    """读取文件并分块"""
    if topic is None:
        topic = filepath.stem
    content = filepath.read_text(encoding="utf-8")
    return chunk_markdown(topic, content)


def chunk_tier_summary(topic: str, content: str, tier_level: int) -> List[Dict]:
    """
    根据 TIER 级别返回不同粒度的索引内容。

    TIER_5/4 (level 5/4): 全量 chunk
    TIER_3 (level 3): 仅摘要句（取前 500 chars per H2 section）
    TIER_2 (level 2): 仅关键词（标题 + 前 200 chars）
    TIER_1 (level 1): 仅 topic name
    """
    if tier_level >= 4:
        return chunk_markdown(topic, content)
    elif tier_level == 3:
        chunks = chunk_markdown(topic, content)
        for c in chunks:
            c["text"] = c["text"][:500]
        return chunks
    elif tier_level == 2:
        # 仅每个 H2 标题 + 第一句话
        lines = content.split("\n")
        summary = []
        for line in lines:
            if line.startswith("## ") or line.startswith("# "):
                summary.append(line)
        text = "\n".join(summary)[:200] if summary else content[:200]
        return [{"topic": topic, "chunk": "摘要", "text": text}]
    else:
        return [{"topic": topic, "chunk": "topic", "text": topic}]


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if path:
        chunks = chunk_file(Path(path))
        for c in chunks:
            print(f"[{c['chunk']}] ({len(c['text'])} chars)")
            print(c['text'][:100] + "...")
            print()
    else:
        # 自测
        test_content = """# test
## 第一章
这是第一章的内容。
## 第二章
这是第二章的内容。
"""
        chunks = chunk_markdown("test", test_content)
        assert len(chunks) == 2
        assert chunks[0]["chunk"] == "第一章"
        assert chunks[1]["chunk"] == "第二章"
        print("✅ chunker self-test passed")
