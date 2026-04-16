"""Cloud model worker via LiteLLM (supports OpenAI, Anthropic, Google, etc.)."""

from __future__ import annotations

import os
import time
from typing import Any

from .base import BaseModelWorker, ModelResponse


def _get_litellm():
    import litellm
    litellm.drop_params = True
    return litellm


class APIModelWorker(BaseModelWorker):
    """Unified cloud model worker using LiteLLM."""

    def __init__(self, model: str, *, api_key: str | None = None, **kwargs: Any):
        super().__init__(model, **kwargs)
        self._api_key = api_key

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> ModelResponse:
        extra: dict[str, Any] = {}
        if self._api_key:
            extra["api_key"] = self._api_key

        litellm = _get_litellm()
        t0 = time.perf_counter()
        resp = await litellm.acompletion(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **extra,
        )
        elapsed = time.perf_counter() - t0

        usage = resp.usage or {}
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        total = getattr(usage, "total_tokens", 0) or (prompt_tokens + completion_tokens)

        try:
            cost = litellm.completion_cost(completion_response=resp)
        except Exception:
            cost = 0.0

        return ModelResponse(
            content=resp.choices[0].message.content or "",
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total,
            elapsed_s=round(elapsed, 2),
            raw={"_cost_usd": cost},
        )

    async def ping(self) -> bool:
        try:
            litellm = _get_litellm()
            extra: dict[str, Any] = {}
            if self._api_key:
                extra["api_key"] = self._api_key
            resp = await litellm.acompletion(
                model=self.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
                **extra,
            )
            return bool(resp.choices)
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        return [self.model]
