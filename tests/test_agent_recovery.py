"""Tests for AgentRecoveryManager + AutoRecoverer."""

from __future__ import annotations

import asyncio

import pytest

from src.leader.agent_instance import AgentInstance, AgentState
from src.leader.agent_pool import AgentPool
from src.leader.agent_recovery import (
    AgentRecoveryManager, AutoRecoverer, RecoveryPolicy,
)
from src.leader.agent_registry import AgentProfile
from src.models.base import BaseModelWorker, ModelResponse


class StubWorker(BaseModelWorker):
    def __init__(self, model="m1"):
        super().__init__(model)

    async def chat(self, messages, *, temperature=0.7, max_tokens=4096):
        return ModelResponse(content="x", model=self.model)

    async def ping(self) -> bool:
        return True

    async def list_models(self) -> list[str]:
        return [self.model]


def _mk_pool_with_one():
    p = AgentProfile(name="a", description="", system_prompt="s")
    pool = AgentPool()
    inst = AgentInstance(profile=p, worker=StubWorker())
    pool.add(inst)
    return pool, inst


def test_success_resets_errors():
    pool, inst = _mk_pool_with_one()
    mgr = AgentRecoveryManager(pool)
    mgr.record_outcome(inst, success=False, error=RuntimeError("x"))
    assert inst.state == AgentState.COOLDOWN
    mgr.record_outcome(inst, success=True)
    assert inst.state == AgentState.IDLE
    assert mgr._state(inst).consecutive_errors == 0


def test_threshold_triggers_error_state():
    pool, inst = _mk_pool_with_one()
    mgr = AgentRecoveryManager(pool, policy=RecoveryPolicy(error_threshold=2))
    mgr.record_outcome(inst, success=False, error=RuntimeError("a"))
    assert inst.state == AgentState.COOLDOWN
    mgr.record_outcome(inst, success=False, error=RuntimeError("b"))
    assert inst.state == AgentState.ERROR


def test_cooldown_expiration_recovers():
    pool, inst = _mk_pool_with_one()
    mgr = AgentRecoveryManager(pool, policy=RecoveryPolicy(cooldown_seconds=0.001))
    mgr.record_outcome(inst, success=False, error=RuntimeError("x"))
    assert inst.state == AgentState.COOLDOWN
    # well past cooldown
    recovered = mgr.recover_expired(now=mgr._state(inst).cooldown_until + 1)
    assert inst.instance_id in recovered
    assert inst.state == AgentState.IDLE


def test_cooldown_not_expired_keeps_state():
    pool, inst = _mk_pool_with_one()
    mgr = AgentRecoveryManager(pool, policy=RecoveryPolicy(cooldown_seconds=60))
    mgr.record_outcome(inst, success=False, error=RuntimeError("x"))
    recovered = mgr.recover_expired(now=mgr._state(inst).cooldown_until - 10)
    assert recovered == []
    assert inst.state == AgentState.COOLDOWN


def test_manually_revive_from_error():
    pool, inst = _mk_pool_with_one()
    mgr = AgentRecoveryManager(pool, policy=RecoveryPolicy(error_threshold=1))
    mgr.record_outcome(inst, success=False, error=RuntimeError("x"))
    assert inst.state == AgentState.ERROR
    mgr.manually_revive(inst)
    assert inst.state == AgentState.IDLE
    assert mgr._state(inst).consecutive_errors == 0


def test_exponential_backoff_grows():
    import time as _t
    pool, inst = _mk_pool_with_one()
    pol = RecoveryPolicy(error_threshold=10, cooldown_seconds=10, cooldown_backoff=2)
    mgr = AgentRecoveryManager(pool, policy=pol)
    delays = []
    for _ in range(3):
        t0 = _t.time()
        mgr.record_outcome(inst, success=False, error=RuntimeError("x"))
        delays.append(mgr._state(inst).cooldown_until - t0)
    # backoff: ~10, ~20, ~40; second should be at least ~1.5x first
    assert delays[1] > delays[0] * 1.5
    assert delays[2] > delays[1] * 1.5


def test_health_snapshot_shape():
    pool, inst = _mk_pool_with_one()
    mgr = AgentRecoveryManager(pool)
    mgr.record_outcome(inst, success=False, error=ValueError("oops"))
    snap = mgr.health_snapshot()
    assert len(snap) == 1
    assert snap[0]["last_error"].startswith("ValueError")
    assert snap[0]["state"] == "cooldown"


def test_auto_recoverer_runs_one_cycle():
    async def _go():
        pool, inst = _mk_pool_with_one()
        mgr = AgentRecoveryManager(pool, policy=RecoveryPolicy(cooldown_seconds=0.01))
        mgr.record_outcome(inst, success=False, error=RuntimeError("x"))
        rec = AutoRecoverer(mgr, interval_s=0.02)
        rec.start()
        # wait long enough for one tick + cooldown to expire
        await asyncio.sleep(0.1)
        await rec.stop()
        return inst.state

    state = asyncio.run(_go())
    assert state == AgentState.IDLE
