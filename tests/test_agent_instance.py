"""Tests for AgentInstance: configuration overrides, state machine, run()."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.leader.agent_instance import AgentInstance, AgentState
from src.leader.agent_registry import AgentProfile
from src.models.base import BaseModelWorker, ModelResponse


class FakeWorker(BaseModelWorker):
    """Records every call; returns a deterministic response."""

    def __init__(self, model: str = "fake-model", *, fail: bool = False):
        super().__init__(model)
        self.calls: list[dict[str, Any]] = []
        self._fail = fail

    async def chat(self, messages, *, temperature=0.7, max_tokens=4096):
        self.calls.append(
            {"messages": list(messages), "temperature": temperature, "max_tokens": max_tokens}
        )
        if self._fail:
            raise RuntimeError("boom")
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        return ModelResponse(content=f"echo:{last_user}", model=self.model)

    async def ping(self): return True
    async def list_models(self): return [self.model]


def _profile(name="generalist", skills=("general",), system="base prompt", T=0.4) -> AgentProfile:
    return AgentProfile(
        name=name,
        description="",
        system_prompt=system,
        required_skills=list(skills),
        temperature=T,
        max_tokens=2048,
    )


def test_overrides_take_precedence():
    p = _profile(name="coder", system="default", T=0.4)
    w = FakeWorker()
    inst = AgentInstance(
        profile=p, worker=w,
        name_override="coder-strict",
        system_prompt_override="strict prompt",
        temperature_override=0.0,
        max_tokens_override=512,
        skills_override=["coding", "review"],
    )
    assert inst.name == "coder-strict"
    assert inst.system_prompt == "strict prompt"
    assert inst.temperature == 0.0
    assert inst.max_tokens == 512
    assert inst.skills == ["coding", "review"]
    assert inst.model == "fake-model"


def test_falls_back_to_profile_when_no_override():
    p = _profile(name="g", T=0.5)
    w = FakeWorker()
    inst = AgentInstance(profile=p, worker=w)
    assert inst.name == "g"
    assert inst.temperature == 0.5
    assert inst.skills == ["general"]


def test_state_machine_transitions():
    inst = AgentInstance(profile=_profile(), worker=FakeWorker())
    assert inst.is_idle and inst.is_available

    inst.mark_busy()
    assert inst.is_busy and not inst.is_idle and not inst.is_available

    inst.mark_idle()
    assert inst.is_idle and inst.last_finished_at is not None

    inst.mark_cooldown()
    assert inst.state == AgentState.COOLDOWN and inst.is_available

    inst.mark_error()
    assert inst.state == AgentState.ERROR and not inst.is_available

    inst.reset()
    assert inst.is_idle and inst.error_count == 0


def test_run_uses_overrides_in_request():
    p = _profile(T=0.4)
    w = FakeWorker()
    inst = AgentInstance(
        profile=p, worker=w,
        temperature_override=0.0, max_tokens_override=128,
        system_prompt_override="STRICT",
    )
    resp = asyncio.run(inst.run("hello"))
    assert resp.content == "echo:hello"
    call = w.calls[0]
    assert call["temperature"] == 0.0
    assert call["max_tokens"] == 128
    # system message includes the override
    assert call["messages"][0]["role"] == "system"
    assert "STRICT" in call["messages"][0]["content"]
    assert call["messages"][-1] == {"role": "user", "content": "hello"}
    assert inst.completed_count == 1
    assert inst.error_count == 0


def test_run_with_extra_system_and_history():
    inst = AgentInstance(profile=_profile(system="BASE"), worker=FakeWorker())
    asyncio.run(inst.run("first turn", record_history=True, extra_system="EXTRA"))
    asyncio.run(inst.run("second turn", use_history=True, record_history=True))

    second_call_msgs = inst.worker.calls[1]["messages"]
    roles = [m["role"] for m in second_call_msgs]
    # system + prior user + prior assistant + new user
    assert roles == ["system", "user", "assistant", "user"]
    assert second_call_msgs[1]["content"] == "first turn"
    assert second_call_msgs[2]["content"] == "echo:first turn"

    # extra_system is single-shot, not persisted
    first_sys = inst.worker.calls[0]["messages"][0]["content"]
    second_sys = inst.worker.calls[1]["messages"][0]["content"]
    assert "EXTRA" in first_sys
    assert "EXTRA" not in second_sys


def test_run_propagates_errors_and_increments_counter():
    inst = AgentInstance(profile=_profile(), worker=FakeWorker(fail=True))
    with pytest.raises(RuntimeError):
        asyncio.run(inst.run("anything"))
    assert inst.error_count == 1
    assert inst.completed_count == 0


def test_run_with_temperature_kwarg_overrides_everything():
    inst = AgentInstance(
        profile=_profile(T=0.4), worker=FakeWorker(),
        temperature_override=0.1,
    )
    asyncio.run(inst.run("hi", temperature=0.9, max_tokens=64))
    call = inst.worker.calls[0]
    assert call["temperature"] == 0.9
    assert call["max_tokens"] == 64


def test_distinct_instances_have_distinct_ids():
    p = _profile()
    w = FakeWorker()
    a = AgentInstance(profile=p, worker=w)
    b = AgentInstance(profile=p, worker=w)
    assert a.instance_id != b.instance_id
    assert a.display_name != b.display_name
