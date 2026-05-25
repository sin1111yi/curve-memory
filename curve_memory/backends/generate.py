#!/usr/bin/env python3
"""generate.py — Ollama text generation backend for semantic degradation.

Calls POST /api/generate instead of /api/embed.
Designed for batch nighttime cron usage — timeout is generous (90s).
"""

import json
import logging
import time
from typing import Optional
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen2.5:3b"
DEFAULT_TIMEOUT = 90
DEFAULT_TEMPERATURE = 0.3

# TIER_3→TIER_2: target ~300 chars
TIER32_PROMPT_EN = (
    "Summarize these technical notes in about 300 characters.\n"
    "Keep only setup commands, config paths, and key parameters:\n\n{content}"
)

TIER32_PROMPT_CN = (
    "将以下技术笔记压缩到约300个字符。\n"
    "只保留安装命令、配置路径和关键参数：\n\n{content}"
)

# TIER_2→TIER_1: target ~100 chars
TIER21_PROMPT_EN = (
    "Extract a one-line summary (≤100 chars) of:\n\n{content}"
)


def _detect_has_cjk(text: str) -> bool:
    """Detect if text contains CJK characters."""
    for ch in text[:200]:
        if '\u4e00' <= ch <= '\u9fff' or '\u3000' <= ch <= '\u303f':
            return True
    return False


class OllamaGenerate:
    """Ollama LLM 调用封装，专为语义降级设计。

    用法:
        gen = OllamaGenerate(model="qwen2.5:3b", timeout=90)
        result = gen.generate("prompt...")
        # {"text": "...", "duration": 16.3, "truncated": False}
    """

    def __init__(self, model: str = DEFAULT_MODEL,
                 base_url: str = "http://localhost:11434",
                 timeout: int = DEFAULT_TIMEOUT,
                 temperature: float = DEFAULT_TEMPERATURE,
                 max_tokens: int = 512):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens

    def _request(self, payload: dict) -> Optional[dict]:
        """Make a request to Ollama's /api/generate endpoint."""
        url = f"{self.base_url}/api/generate"
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            logger.debug("Ollama HTTP error: %s — %s", e.code, body[:200])
            return None
        except Exception as e:
            logger.debug("Ollama request error: %s", e)
            return None

    def generate(self, prompt: str, max_retries: int = 1,
                 num_predict: Optional[int] = None) -> Optional[dict]:
        """Call Ollama generate API.

        Args:
            prompt: The prompt text.
            max_retries: Number of retries on failure (default 1).
            num_predict: Max tokens for this call (overrides instance default).

        Returns:
            {"text": str, "duration": float, "truncated": bool} or None on failure.
        """
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": num_predict if num_predict is not None else self.max_tokens,
            }
        }

        for attempt in range(max_retries + 1):
            t0 = time.time()
            result = self._request(payload)
            elapsed = time.time() - t0

            if result and "response" in result:
                text = result["response"].strip()
                return {
                    "text": text,
                    "duration": round(elapsed, 1),
                    "truncated": result.get("done", True) and elapsed >= self.timeout * 0.9,
                }

            if attempt < max_retries:
                logger.debug("Retrying Ollama generate (attempt %d)", attempt + 1)

        return None

    def summarize_tier32(self, content: str) -> Optional[str]:
        """TIER_3→TIER_2 summarization. Target: ~300 chars.

        Tries English prompt first; if content is CJK-heavy, falls back to Chinese.
        """
        if _detect_has_cjk(content):
            prompt = TIER32_PROMPT_CN.format(content=content)
        else:
            prompt = TIER32_PROMPT_EN.format(content=content)

        result = self.generate(prompt)
        if result and result["text"]:
            text = result["text"]
            # Truncate if still too long
            if len(text) > 350:
                text = text[:350]
            return text
        return None

    def summarize_tier21(self, content: str) -> Optional[str]:
        """TIER_2→TIER_1 summarization. Target: ~100 chars."""
        prompt = TIER21_PROMPT_EN.format(content=content)
        result = self.generate(prompt, num_predict=150)
        if result and result["text"]:
            text = result["text"]
            if len(text) > 120:
                text = text[:120]
            return text
        return None
