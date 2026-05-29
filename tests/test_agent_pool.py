"""Tests for AgentPool: same-model-different-config, scheduling, lease."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.config import AppConfig, LeaderConfig, WorkerEntry
from src.leader.agent_instance import AgentInstance, AgentState
from src.leader.agent_pool import (
    AgentPool, NoAgentAvailableError, NoMatchingAgentError,
)
from src.leader.agent_registry import AgentProfile, AgentRegistry
from src.models.base import BaseModelWorker, ModelResponse


class FakeWorker(BaseModelWorker):
    def __init__(self, model: str = "fake-model", *, delay: float = 0.0):
        super().__init__(model)
        self.calls: list[dict[str, Any]] = []
        self._delay = delay

    async def chat(self, messages, *, temperature=0.7, max_tokens=4096):
        if self._delay:
            await asyncio.sleep(self._delay)
        self.calls.append(
            {"temperature": temperature, "max_tokens": max_tokens, "messages": list(messages)}
        )
        return ModelResponse(content="ok", model=self.model)

    async def ping(self): return True
    async def list_models(self): return [self.model]


def _profile(name="coder", skills=("coding",), system="base", T=0.4) -> AgentProfile:
    return AgentProfile(
        name=name, description="", system_prompt=system,
        required_skills=list(skills), temperature=T, max_tokens=2048,
        preferred_models=["fake-model"],
    )


def _registry_with(profiles: list[AgentProfile]) -> AgentRegistry:
    reg = AgentRegistry.__new__(AgentRegistry)
    reg.agents_dir = None  # type: ignore[attr-defined]
    reg._cache = {p.name: p for p in profiles}
    return reg


# ── Same model + different config = different agents ─────────────────


def test_same_model_different_config_creates_distinct_agents():
    pool = AgentPool()
    profile = _profile(name="coder", T=0.4)
    shared_worker = FakeWorker("fake-model")

    pool.add_from_profile(
        profile, worker=shared_worker,
        name_override="coder-strict", temperature_override=0.0,
    )
    pool.add_from_profile(
        profile, worker=shared_worker,
        name_override="coder-creative", temperature_override=0.9,
    )

    assert len(pool) == 2
    a, b = pool.all()
    assert a.model == b.model == "fake-model"     # 同一个底层模型
    assert a.name != b.name                         # 不同的 Agent 身份
    assert a.temperature != b.temperature           # 不同的运行参数
    assert a.instance_id != b.instance_id           # 独立实例


def test_add_variant_via_registry():
    reg = _registry_with([_profile(name="coder", T=0.4)])
    pool = AgentPool(registry=reg)
    shared = FakeWorker("fake-model")

    strict = pool.add_variant("coder", worker=shared, name="coder-strict", temperature=0.0)
    creative = pool.add_variant("coder", worker=shared, name="coder-creative", temperature=0.9)

    assert strict[0].name == "coder-strict" and strict[0].temperature == 0.0
    assert creative[0].name == "coder-creative" and creative[0].temperature == 0.9
    # 同模型独立调度状态
    strict[0].mark_busy()
    assert creative[0].is_idle


def test_add_variant_count_clones():
    reg = _registry_with([_profile(name="g", skills=("general",))])
    pool = AgentPool(registry=reg)
    workers = pool.add_variant("g", worker=FakeWorker(), count=3)
    assert len(workers) == 3
    ids = {w.instance_id for w in workers}
    assert len(ids) == 3   # all distinct


# ── Scheduling ───────────────────────────────────────────────────────


def test_acquire_picks_idle_with_skill_match():
    pool = AgentPool()
    pool.add_from_profile(_profile(name="coder", skills=("coding",)), worker=FakeWorker())
    pool.add_from_profile(_profile(name="writer", skills=("writing",)), worker=FakeWorker())

    async def go():
        a = await pool.acquire(["coding"])
        assert a.name == "coder" and a.is_busy
        await pool.release(a)
        assert a.is_idle

    asyncio.run(go())


def test_no_matching_agent_raises():
    pool = AgentPool()
    pool.add_from_profile(_profile(name="coder", skills=("coding",)), worker=FakeWorker())

    async def go():
        with pytest.raises(NoMatchingAgentError):
            await pool.acquire(["dancing"])

    asyncio.run(go())


def test_acquire_waits_until_release():
    pool = AgentPool()
    pool.add_from_profile(_profile(name="coder", skills=("coding",)), worker=FakeWorker())

    async def go():
        first = await pool.acquire(["coding"])
        assert first.is_busy

        async def releaser():
            await asyncio.sleep(0.05)
            await pool.release(first)

        # second waits, then succeeds once releaser fires
        _, second = await asyncio.gather(releaser(), pool.acquire(["coding"]))
        assert second.instance_id == first.instance_id
        await pool.release(second)

    asyncio.run(go())


def test_acquire_timeout_raises_no_agent_available():
    pool = AgentPool()
    pool.add_from_profile(_profile(name="coder", skills=("coding",)), worker=FakeWorker())

    async def go():
        a = await pool.acquire(["coding"])  # only one, now busy
        assert a.is_busy
        with pytest.raises(NoAgentAvailableError):
            await pool.acquire(["coding"], timeout=0.05)
        await pool.release(a)

    asyncio.run(go())


def test_lease_releases_on_exception():
    pool = AgentPool()
    pool.add_from_profile(_profile(name="coder", skills=("coding",)), worker=FakeWorker())

    async def go():
        with pytest.raises(RuntimeError):
            async with pool.lease(["coding"]) as a:
                assert a.is_busy
                raise RuntimeError("user code blew up")
        # Even after exception, the agent should be released back to idle
        assert pool.all()[0].is_idle

    asyncio.run(go())


def test_dispatch_runs_and_releases():
    pool = AgentPool()
    pool.add_from_profile(
        _profile(name="coder", skills=("coding",), T=0.0), worker=FakeWorker(),
    )

    async def go():
        agent, resp = await pool.dispatch("write hello", required_skills=["coding"])
        assert resp.content == "ok"
        assert agent.is_idle
        # Verified that override temperature was used
        assert agent.worker.calls[0]["temperature"] == 0.0

    asyncio.run(go())


def test_load_balancing_prefers_less_busy_among_equals():
    pool = AgentPool()
    p = _profile(name="coder", skills=("coding",))
    pool.add_from_profile(p, worker=FakeWorker(), count=2)

    async def go():
        a1 = await pool.acquire(["coding"])
        await pool.release(a1)
        # a1 has completed_count=0 still (we didn't call run); manually bump:
        a1.completed_count = 5
        # Now acquire again — the *other* one should be preferred
        a2 = await pool.acquire(["coding"])
        assert a2.instance_id != a1.instance_id
        await pool.release(a2)

    asyncio.run(go())


def test_concurrent_dispatch_uses_distinct_agents():
    pool = AgentPool()
    p = _profile(name="coder", skills=("coding",))
    pool.add_from_profile(p, worker=FakeWorker(delay=0.05), count=2)

    async def go():
        results = await asyncio.gather(
            pool.dispatch("a", required_skills=["coding"]),
            pool.dispatch("b", required_skills=["coding"]),
        )
        ids = {agent.instance_id for agent, _ in results}
        assert len(ids) == 2  # ran in parallel on two distinct instances

    asyncio.run(go())


# ── Profile resolution path ──────────────────────────────────────────


def test_add_from_profile_resolves_model_via_cfg(monkeypatch):
    cfg = AppConfig(
        leader=LeaderConfig(model="leader", ollama_base_url="http://x"),
        workers_local=[WorkerEntry(model="fake-model", provider="ollama")],
    )
    captured: dict[str, Any] = {}

    def fake_factory(profile, model):
        captured["model"] = model
        return FakeWorker(model)

    pool = AgentPool(cfg=cfg, worker_factory=fake_factory)
    p = _profile(name="coder", skills=("coding",))
    pool.add_from_profile(p)  # no worker, no model → should resolve to "fake-model"
    assert captured["model"] == "fake-model"


def test_add_from_profile_raises_when_no_model_resolvable():
    pool = AgentPool(cfg=AppConfig())  # empty config, profile has fake-model only
    p = _profile(name="coder")
    with pytest.raises(ValueError):
        pool.add_from_profile(p)
