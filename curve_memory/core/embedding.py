"""
Embedding Provider ABC + factory + utilities
"""

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional


class EmbeddingProvider(ABC):
    """Embedding vectorization ABC"""

    @abstractmethod
    def embed(self, text: str) -> List[float]:
        ...

    @abstractmethod
    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        ...

    @property
    @abstractmethod
    def dim(self) -> int:
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Cosine similarity"""
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def load_embedding_index(embedding_dir: Path) -> Dict[str, List[Dict[str, Any]]]:
    """Load all topic embedding vectors from .embedding_index/"""
    index = {}
    if not embedding_dir.exists():
        return index
    for fpath in embedding_dir.glob("*.jsonl"):
        topic = fpath.stem
        chunks = []
        for line in fpath.read_text(encoding="utf-8").strip().splitlines():
            if line.strip():
                chunks.append(json.loads(line))
        if chunks:
            index[topic] = chunks
    return index


def create_embedding_provider(config: dict = None) -> Optional[EmbeddingProvider]:
    """Create Ollama embedding backend from config"""
    if config is None:
        config = {}

    model = config.get("model", "qwen3-embedding:8b")
    base_url = config.get("base_url", "http://localhost:11434")

    from curve_memory.backends.ollama import OllamaBackend
    try:
        provider = OllamaBackend(model=model, base_url=base_url)
        return provider
    except Exception:
        pass
    return None
