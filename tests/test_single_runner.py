"""Tests for single_runner safety wrapper."""

from __future__ import annotations

import asyncio
from typing import Any

from src.cli.single_runner import (
    SingleRunOutcome, is_remote_worker, run_single_safely,
)
from src.config import AppConfig, PrivacyConfig
from src.models.base import BaseModelWorker, ModelResponse
from src.privacy.guard import PrivacyGuard, SanitizeResult, SensitiveSpan


# ── Fakes ────────────────────────────────────────────────────────────


class FakeOllama(BaseModelWorker):
    def __init__(self, model="local-7b"):
        super().__init__(model)
        self.last_messages: list[dict[str, str]] | None = None

    async def chat(self, messages, *, temperature=0.7, max_tokens=4096):
        self.last_messages = list(messages)
        # echo last user message back so we can detect placeholders
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        return ModelResponse(content=f"resp: {last_user}", model=self.model)

    async def ping(self): return True
    async def list_models(self): return [self.model]

# trick is_remote_worker by name
FakeOllama.__name__ = "OllamaWorker"


class FakeAPI(BaseModelWorker):
    def __init__(self, model="gpt-fake"):
        super().__init__(model)
        self.last_messages: list[dict[str, str]] | None = None

    async def chat(self, messages, *, temperature=0.7, max_tokens=4096):
        self.last_messages = list(messages)
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        return ModelResponse(content=f"echo: {last_user}", model=self.model)

    async def ping(self): return True
    async def list_models(self): return [self.model]

FakeAPI.__name__ = "APIModelWorker"


class StubGuard:
    """Deterministic guard: replaces 'SECRET' with [TOK_xx]."""

    def sanitize(self, text):
        if "SECRET" not in text:
            return SanitizeResult(original=text, sanitized=text)
        ph = "[TOK_aaaaaaaa]"
        sanitized = text.replace("SECRET", ph)
        spans = [SensitiveSpan(entity_type="TOK", start=0, end=0, text="SECRET", placeholder=ph)]
        return SanitizeResult(
            original=text, sanitized=sanitized, spans=spans,
            has_sensitive=True, placeholder_map={ph: "SECRET"},
        )

    def restore(self, text, placeholder_map):
        for ph, real in placeholder_map.items():
            text = text.replace(ph, real)
        return text


# ── Tests ────────────────────────────────────────────────────────────


def test_is_remote_worker_classification():
    assert is_remote_worker(FakeOllama()) is False
    assert is_remote_worker(FakeAPI()) is True


def test_local_worker_skips_sanitize():
    w = FakeOllama()
    cfg = AppConfig(privacy=PrivacyConfig(enabled=True))
    out = asyncio.run(run_single_safely(
        w, [{"role": "user", "content": "my SECRET data"}], cfg=cfg, guard=StubGuard(),
    ))
    assert out.sanitized is False
    assert "SECRET" in w.last_messages[-1]["content"]
    assert out.response.content == "resp: my SECRET data"


def test_remote_worker_sanitizes_and_restores():
    w = FakeAPI()
    cfg = AppConfig(privacy=PrivacyConfig(enabled=True))
    out = asyncio.run(run_single_safely(
        w, [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "my SECRET data"},
        ],
        cfg=cfg, guard=StubGuard(),
    ))
    # Worker received the sanitized version (no SECRET)
    sent_user = w.last_messages[-1]["content"]
    assert "SECRET" not in sent_user
    assert "[TOK_" in sent_user
    # Final response is restored back to plaintext for the user
    assert out.sanitized is True
    assert "SECRET" in out.response.content
    assert "[TOK_" not in out.response.content


def test_disabled_privacy_does_not_sanitize_even_for_remote():
    w = FakeAPI()
    cfg = AppConfig(privacy=PrivacyConfig(enabled=False))
    out = asyncio.run(run_single_safely(
        w, [{"role": "user", "content": "my SECRET data"}], cfg=cfg,
    ))
    assert out.sanitized is False
    assert "SECRET" in w.last_messages[-1]["content"]


def test_no_user_message_is_safe():
    w = FakeAPI()
    cfg = AppConfig(privacy=PrivacyConfig(enabled=True))
    out = asyncio.run(run_single_safely(
        w, [{"role": "system", "content": "hi"}], cfg=cfg, guard=StubGuard(),
    ))
    assert out.sanitized is False
