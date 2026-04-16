"""Token counting, cost estimation, and routing decision logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import tiktoken

# Cost per million tokens (input / output) — update as prices change.
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.00, 30.00),
    "o1": (15.00, 60.00),
    "o1-mini": (3.00, 12.00),
    "claude-3-5-sonnet-20241022": (3.00, 15.00),
    "claude-3-opus-20240229": (15.00, 75.00),
    "claude-3-haiku-20240307": (0.25, 1.25),
    "gemini-1.5-pro": (1.25, 5.00),
    "gemini-1.5-flash": (0.075, 0.30),
    "gemini-2.0-flash": (0.10, 0.40),
}

# Local models are always free.
FREE_PROVIDERS = {"ollama"}


def count_tokens(text: str, model: str = "gpt-4o") -> int:
    """Count tokens using tiktoken (falls back to word-based estimate)."""
    try:
        enc = tiktoken.encoding_for_model(model)
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text) // 4)


def estimate_cost(
    prompt_tokens: int,
    completion_tokens: int,
    model: str,
) -> float:
    """Return estimated cost in USD for a single request."""
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        return 0.0
    input_price, output_price = pricing
    cost = (prompt_tokens * input_price + completion_tokens * output_price) / 1_000_000
    return round(cost, 6)


@dataclass
class CostTracker:
    """Tracks cumulative cost for a session or period."""

    budget_usd: float = 20.0
    spent_usd: float = 0.0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.budget_usd - self.spent_usd)

    @property
    def over_budget(self) -> bool:
        return self.spent_usd >= self.budget_usd

    def record(self, prompt_tokens: int, completion_tokens: int, cost_usd: float) -> None:
        self.total_prompt_tokens += prompt_tokens
        self.total_completion_tokens += completion_tokens
        self.spent_usd += cost_usd

    def to_dict(self) -> dict[str, Any]:
        return {
            "budget_usd": self.budget_usd,
            "spent_usd": round(self.spent_usd, 6),
            "remaining_usd": round(self.remaining_usd, 6),
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
        }


def should_use_local(
    importance: int,
    threshold_local: int = 5,
    budget_remaining: float = 20.0,
) -> bool:
    """Decide whether a subtask should be routed to a free local model.

    Returns True when the task is low-importance OR the budget is exhausted.
    """
    if budget_remaining <= 0:
        return True
    return importance < threshold_local


def pick_tier(
    importance: int,
    threshold_local: int = 5,
    threshold_best: int = 8,
) -> str:
    """Return 'local', 'mid', or 'best' based on importance score."""
    if importance < threshold_local:
        return "local"
    if importance >= threshold_best:
        return "best"
    return "mid"
