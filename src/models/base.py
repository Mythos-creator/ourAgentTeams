"""Abstract base for all model workers (local and cloud)."""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModelResponse:
    content: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    elapsed_s: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def cost_usd(self) -> float:
        return self.raw.get("_cost_usd", 0.0)


class BaseModelWorker(abc.ABC):
    """Every model worker (local or API) implements this interface."""

    def __init__(self, model: str, **kwargs: Any):
        self.model = model
        self._kwargs = kwargs

    @abc.abstractmethod
    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> ModelResponse:
        """Send a chat completion request and return a ModelResponse."""
        ...

    @abc.abstractmethod
    async def ping(self) -> bool:
        """Lightweight health check — returns True if model is reachable."""
        ...

    @abc.abstractmethod
    async def list_models(self) -> list[str]:
        """Return available model names for this provider."""
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(model={self.model!r})"
