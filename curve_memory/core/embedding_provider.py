#!/usr/bin/env python3
"""
embedding_provider.py — Embedding Provider 抽象 + 实现

支持：
- OllamaProvider: 通过 Ollama API 获取 embedding
- 预留：SentenceTransformersProvider（需要安装 PyTorch）
"""

import json
import os
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional, Dict
import urllib.request
import urllib.error


class EmbeddingProvider(ABC):
    """Embedding Provider 抽象基类"""

    @abstractmethod
    def embed(self, text: str) -> List[float]:
        """单文本嵌入"""
        ...

    @abstractmethod
    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """批量文本嵌入"""
        ...

    @property
    @abstractmethod
    def dim(self) -> int:
        """返回向量维度"""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider 名称"""
        ...


class OllamaProvider(EmbeddingProvider):
    """Ollama Embedding Provider"""

    def __init__(self, model: str = "nomic-embed-text",
                 base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._dim = None
        self._name = f"ollama/{model}"

    def _request(self, endpoint: str, data: dict) -> dict:
        url = f"{self.base_url}/api/{endpoint}"
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode() if e.fp else ""
            raise RuntimeError(f"Ollama API error {e.code}: {body}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Ollama not reachable: {e.reason}")

    def embed(self, text: str) -> List[float]:
        result = self._request("embeddings", {
            "model": self.model,
            "prompt": text,
        })
        return result.get("embedding", [])

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        # Ollama 不支持原生 batch，串行调用
        return [self.embed(t) for t in texts]

    @property
    def dim(self) -> int:
        if self._dim is None:
            # 测试获取维度
            test_vec = self.embed("test")
            self._dim = len(test_vec)
        return self._dim

    @property
    def name(self) -> str:
        return self._name


def create_embedding_provider(config: dict = None) -> Optional[EmbeddingProvider]:
    """
    Provider 工厂函数。
    返回 None 表示不可用（降级）。
    """
    if config is None:
        config = {}

    provider_name = config.get("provider", "ollama")
    model = config.get("model", "qwen3-embedding:8b")
    base_url = config.get("base_url", "http://localhost:11434")

    if provider_name == "ollama":
        try:
            provider = OllamaProvider(model=model, base_url=base_url)
            # 测试连接
            test = provider.embed("ping")
            if test and len(test) > 0:
                print(f"  ✅ Ollama {model} ready (dim={len(test)})")
                return provider
            else:
                print("  ⚠️  Ollama returned empty embedding")
                return None
        except Exception as e:
            print(f"  ⚠️  Ollama not available: {e}")
            return None

    print(f"  ⚠️  Unknown embedding provider: {provider_name}")
    return None


# 预计算余弦相似度
def cosine_similarity(a: List[float], b: List[float]) -> float:
    """计算两个向量的余弦相似度"""
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def load_embedding_index(index_dir: Path) -> Dict[str, List[Dict]]:
    """
    加载 .embedding_index/ 目录下的所有 .jsonl 文件。

    返回: {topic: [{chunk, text, vector}, ...]}
    """
    index = {}
    if not index_dir.exists():
        return index
    for fpath in sorted(index_dir.glob("*.jsonl")):
        topic = fpath.stem
        chunks = []
        for line in fpath.read_text(encoding="utf-8").strip().splitlines():
            if line.strip():
                data = json.loads(line)
                chunks.append(data)
        if chunks:
            index[topic] = chunks
    return index


if __name__ == "__main__":
    # 测试
    provider = create_embedding_provider()
    if provider:
        v1 = provider.embed("hello world")
        v2 = provider.embed("hello world")
        v3 = provider.embed("goodbye world")
        sim_12 = cosine_similarity(v1, v2)
        sim_13 = cosine_similarity(v1, v3)
        print(f"  dim={provider.dim}")
        print(f"  cos('hello','hello') = {sim_12:.4f}")
        print(f"  cos('hello','goodbye') = {sim_13:.4f}")
        assert sim_12 > 0.99
        assert sim_13 < sim_12
        print("  ✅ Embedding self-test passed")
    else:
        print("  ⚠️  No embedding provider available")
