"""Tests for RetryingWorker."""

from __future__ import annotations

import asyncio

import pytest

from src.models.base import BaseModelWorker, ModelResponse
from src.models.retry import RetryingWorker


class FlakyWorker(BaseModelWorker):
    def __init__(self, *, fails: int, exc_factory):
        super().__init__("flaky")
        self.fails_remaining = fails
        self.exc_factory = exc_factory
        self.calls = 0

    async def chat(self, messages, *, temperature=0.7, max_tokens=4096):
        self.calls += 1
        if self.fails_remaining > 0:
            self.fails_remaining -= 1
            raise self.exc_factory()
        return ModelResponse(content="ok", model=self.model)

    async def ping(self): return True
    async def list_models(self): return ["flaky"]


class _RateLimitError(Exception):
    pass


class _ValueLikeError(ValueError):
    pass


def test_succeeds_after_retries():
    inner = FlakyWorker(fails=2, exc_factory=_RateLimitError)
    w = RetryingWorker(inner, max_attempts=3, base_delay=0.001)
    resp = asyncio.run(w.chat([{"role": "user", "content": "hi"}]))
    assert resp.content == "ok"
    assert inner.calls == 3


def test_gives_up_after_max_attempts():
    inner = FlakyWorker(fails=99, exc_factory=_RateLimitError)
    w = RetryingWorker(inner, max_attempts=2, base_delay=0.001)
    with pytest.raises(_RateLimitError):
        asyncio.run(w.chat([{"role": "user", "content": "hi"}]))
    assert inner.calls == 2


def test_does_not_retry_non_retryable_errors():
    inner = FlakyWorker(fails=99, exc_factory=_ValueLikeError)
    w = RetryingWorker(inner, max_attempts=5, base_delay=0.001)
    with pytest.raises(_ValueLikeError):
        asyncio.run(w.chat([{"role": "user", "content": "hi"}]))
    assert inner.calls == 1


def test_custom_retryable_predicate():
    inner = FlakyWorker(fails=1, exc_factory=_ValueLikeError)
    w = RetryingWorker(
        inner, max_attempts=3, base_delay=0.001,
        retryable=lambda e: isinstance(e, _ValueLikeError),
    )
    resp = asyncio.run(w.chat([{"role": "user", "content": "hi"}]))
    assert resp.content == "ok"
    assert inner.calls == 2


def test_ping_and_list_pass_through():
    inner = FlakyWorker(fails=0, exc_factory=_RateLimitError)
    w = RetryingWorker(inner)
    assert asyncio.run(w.ping()) is True
    assert asyncio.run(w.list_models()) == ["flaky"]
