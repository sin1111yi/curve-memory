#!/usr/bin/env python3
"""
search.py — 三路混合检索核心

最终排序分 = α · BM25_score + β · cosine_sim + γ · R(t)
默认权重: α=0.35, β=0.45, γ=0.20
"""

import json
import math
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from curve_memory.core.tier import forgetting_curve, r_to_tier_name, r_to_tier_level
from curve_memory.core.activity import parse_activity

# === 默认权重 ===
ALPHA = 0.35  # BM25
BETA = 0.45   # Embedding cosine
GAMMA = 0.20  # R(t)

# === 降级级别定义 ===
DEGRADE_LEVELS = {
    0: "Full: BM25 + Embedding + R(t)",
    1: "BM25 + R(t) (no embedding)",
    2: "Embedding + R(t) (no FTS5)",
    3: "R(t) only (keyword match on topic name)",
    4: "Fallback: pure idx keyword match",
}


class HybridSearch:
    """三路混合检索器"""

    def __init__(self, memories_dir: Path, embedder=None,
                 alpha: float = ALPHA, beta: float = BETA, gamma: float = GAMMA):
        self.memories_dir = Path(memories_dir)
        self.active_dir = self.memories_dir / "active"
        self.embedding_dir = self.memories_dir / ".embedding_index"
        self.fts5_path = self.memories_dir / ".fts5" / "curve_memory_fts5.db"
        self.activity_path = self.memories_dir / "ACTIVITY.yaml"
        self.embedder = embedder
        self._alpha = alpha
        self._beta = beta
        self._gamma = gamma
        self.degrade_level = self._detect_degrade_level()

    def _detect_degrade_level(self) -> int:
        """检测当前降级级别（0-4）"""
        has_fts5 = self.fts5_path.exists()
        has_embedding = self.embedding_dir.exists() and any(self.embedding_dir.iterdir())
        has_embedder = self.embedder is not None

        if has_fts5 and has_embedder and has_embedding:
            return 0  # 三路全开
        elif has_fts5 and not has_embedder:
            return 1  # 仅 BM25 + R(t)
        elif has_embedder and has_embedding and not has_fts5:
            return 2  # 仅 Embedding + R(t)
        elif has_fts5 or has_embedder:
            return 3  # 仅单路 + R(t)
        else:
            return 4  # 纯 idx 关键词匹配

    def search(self, query: str, top_k: int = 5,
               alpha: float = None, beta: float = None, gamma: float = None
               ) -> List[Tuple[str, float, str, float]]:
        """
        三路混合检索。

        返回: [(topic, score, snippet, r_value), ...] 按分数降序
        """
        if not query.strip():
            return []

        # 获取所有活跃 topic
        activity = self._load_activity()
        if not activity:
            return []

        all_topics = list(activity.keys())

        # 三路并行
        bm25_scores = self._bm25_search(query, all_topics) if self.degrade_level <= 1 else {}
        cosine_scores = self._semantic_search(query, all_topics) if self.degrade_level in (0, 2) else {}
        # Level 3/4: 关键词匹配 topic name
        keyword_scores = {}
        if self.degrade_level >= 3:
            query_lower = query.lower()
            for topic in all_topics:
                if query_lower in topic.lower():
                    keyword_scores[topic] = 1.0

        # Compute R(t) from timestamp if available, otherwise use raw t value
        now = time.time()
        r_values = {}
        for t, info in activity.items():
            raw_t = info.get("t", 0)
            # If t is a Unix timestamp (>= 1e12), compute delta in days
            if isinstance(raw_t, (int, float)) and raw_t > 1000000000000:
                t_days = (now - raw_t) / 86400
            else:
                t_days = raw_t
            r_values[t] = forgetting_curve(t_days)

        # 归一化
        bm25_norm = self._normalize_minmax(bm25_scores)
        cos_norm = self._normalize_minmax(cosine_scores)
        # R(t) 归一化到 [0, 1]
        r_norm = {}
        if r_values:
            max_r = max(r_values.values()) if r_values else 1.0
            min_r = min(r_values.values()) if r_values else 0.462
            span = max_r - min_r if max_r > min_r else 1.0
            r_norm = {t: (v - min_r) / span for t, v in r_values.items()}

        # 融合
        alpha = alpha if alpha is not None else getattr(self, '_alpha', ALPHA)
        beta = beta if beta is not None else getattr(self, '_beta', BETA)
        gamma = gamma if gamma is not None else getattr(self, '_gamma', GAMMA)
        all_scores = {}
        for topic in all_topics:
            score = (alpha * bm25_norm.get(topic, 0) +
                     beta * cos_norm.get(topic, 0) +
                     gamma * r_norm.get(topic, 0) +
                     keyword_scores.get(topic, 0))
            if score > 0:
                all_scores[topic] = score

        # 如果没有命中任何检索，用 R(t) 排序保底
        if not all_scores:
            all_scores = {t: r_norm.get(t, 0) for t in all_topics}

        # 排序取 top_k
        sorted_topics = sorted(all_scores.items(), key=lambda x: -x[1])[:top_k]

        # 获取 snippet
        result = []
        for topic, score in sorted_topics:
            r_val = r_values.get(topic, 0.462)
            snippet = self._get_snippet(topic, r_val)
            result.append((topic, score, snippet, r_val))

        return result

    def _bm25_search(self, query: str, topics: List[str]) -> Dict[str, float]:
        """FTS5 BM25 检索"""
        if not self.fts5_path.exists():
            return {}
        try:
            conn = sqlite3.connect(str(self.fts5_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # 用 FTS5 MATCH
            fts_query = " OR ".join(query.split())
            cursor.execute(
                "SELECT topic, rank FROM memory_fts WHERE content MATCH ? ORDER BY rank LIMIT 20",
                (fts_query,)
            )
            rows = cursor.fetchall()
            conn.close()

            # BM25 rank 是负值（越小越相关），转为正分
            scores = {}
            for row in rows:
                topic = row["topic"]
                rank = row["rank"]
                # rank 是负值，越小越相关；转为正分
                scores[topic] = -rank

            return scores
        except Exception:
            return {}

    def _semantic_search(self, query: str, topics: List[str]) -> Dict[str, float]:
        """Embedding 语义检索"""
        if not self.embedder or not self.embedding_dir.exists():
            return {}

        try:
            query_vec = self.embedder.embed(query)
            if not query_vec:
                return {}

            # 加载索引
            from curve_memory.core.embedding import load_embedding_index, cosine_similarity
            index = load_embedding_index(self.embedding_dir)

            # 对每个 topic 取最大 chunk 相似度
            scores = {}
            for topic in topics:
                chunks = index.get(topic, [])
                if not chunks:
                    continue
                max_sim = 0.0
                for chunk in chunks:
                    vec = chunk.get("vector")
                    if vec:
                        sim = cosine_similarity(query_vec, vec)
                        max_sim = max(max_sim, sim)
                if max_sim > 0:
                    scores[topic] = max_sim

            return scores
        except Exception as e:
            print(f"  ⚠️  Semantic search error: {e}")
            return {}

    def _load_activity(self) -> dict:
        """加载 ACTIVITY.yaml"""
        if not self.activity_path.exists():
            return {}
        raw = self.activity_path.read_text(encoding="utf-8")
        data = parse_activity(raw)
        return data.get("memories", {})

    def _normalize_minmax(self, scores: Dict[str, float]) -> Dict[str, float]:
        if not scores:
            return {}
        max_s = max(scores.values())
        min_s = min(scores.values())
        span = max_s - min_s if max_s > min_s else 1.0
        return {k: (v - min_s) / span for k, v in scores.items()}

    def _get_snippet(self, topic: str, r: float) -> str:
        """根据 TIER 获取内容片段"""
        tier_level = r_to_tier_level(r)
        filepath = self.active_dir / f"{topic}.md"
        if not filepath.exists():
            return ""

        content = filepath.read_text(encoding="utf-8")

        if tier_level >= 4:
            # 完整内容，取前 500 chars
            return content[:500]
        elif tier_level == 3:
            # 摘要
            lines = [l for l in content.split("\n") if l.strip()]
            return "\n".join(lines[:5])[:300]
        elif tier_level == 2:
            # 首行
            first_line = content.split("\n")[0] if content else ""
            return first_line[:200]
        else:
            return f"[{topic}]"

    @property
    def degrade_info(self) -> str:
        return DEGRADE_LEVELS.get(self.degrade_level, "Unknown")


if __name__ == "__main__":
    # 测试
    memories_dir = Path.home() / ".hermes" / "memories"
    searcher = HybridSearch(memories_dir)
    print(f"Degrade level: {searcher.degrade_level} — {searcher.degrade_info}")

    # 简单关键词检索
    results = searcher.search("R(t) 遗忘曲线")
    print(f"\nSearch results for 'R(t) 遗忘曲线':")
    for topic, score, snippet, r in results:
        tier = r_to_tier_name(r)
        print(f"  {topic}: score={score:.4f}, R={r:.4f} ({tier})")
        print(f"    {snippet[:80]}...")
