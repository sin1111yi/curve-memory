"""
Ollama Embedding Backend — 通过 Ollama API 获取向量
"""

import json
from typing import List
import urllib.request
import urllib.error

from ..core.embedding_provider import EmbeddingProvider


class OllamaBackend(EmbeddingProvider):
    def __init__(self, model: str = "nomic-embed-text",
                 base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._dim = None

    def _request(self, endpoint: str, data: dict) -> dict:
        url = f"{self.base_url}/api/{endpoint}"
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())

    def embed(self, text: str) -> List[float]:
        result = self._request("embeddings", {
            "model": self.model,
            "prompt": text,
        })
        return result.get("embedding", [])

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = len(self.embed("test"))
        return self._dim

    @property
    def name(self) -> str:
        return f"ollama/{self.model}"
