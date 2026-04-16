"""Ollama-based local model worker."""

from __future__ import annotations

import time
from typing import Any

from .base import BaseModelWorker, ModelResponse


def _get_ollama():
    import ollama
    return ollama


class OllamaWorker(BaseModelWorker):
    """Wraps the Ollama Python SDK for local LLM inference."""

    def __init__(self, model: str, *, base_url: str = "http://localhost:11434", **kwargs: Any):
        super().__init__(model, **kwargs)
        self._base_url = base_url
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            ollama = _get_ollama()
            self._client = ollama.AsyncClient(host=self._base_url)
        return self._client

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> ModelResponse:
        client = self._ensure_client()
        t0 = time.perf_counter()
        resp = await client.chat(
            model=self.model,
            messages=messages,
            options={"temperature": temperature, "num_predict": max_tokens},
        )
        elapsed = time.perf_counter() - t0

        prompt_tokens = resp.get("prompt_eval_count", 0) or 0
        completion_tokens = resp.get("eval_count", 0) or 0

        return ModelResponse(
            content=resp["message"]["content"],
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            elapsed_s=round(elapsed, 2),
            raw={"_cost_usd": 0.0, **resp},
        )

    async def ping(self) -> bool:
        try:
            client = self._ensure_client()
            models = await client.list()
            names = [m.get("name", m.get("model", "")) for m in models.get("models", [])]
            return any(self.model in n for n in names)
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        try:
            client = self._ensure_client()
            resp = await client.list()
            return [m.get("name", m.get("model", "")) for m in resp.get("models", [])]
        except Exception:
            return []
