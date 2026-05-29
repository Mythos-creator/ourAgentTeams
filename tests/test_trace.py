"""Tests for TracingWorker + write_trace."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from src.models.base import BaseModelWorker, ModelResponse
from src.utils.trace import TracingWorker, write_trace


class FakeWorker(BaseModelWorker):
    def __init__(self, model="fake-1", *, fail=False):
        super().__init__(model)
        self.fail = fail

    async def chat(self, messages, *, temperature=0.7, max_tokens=4096):
        if self.fail:
            raise RuntimeError("boom")
        return ModelResponse(
            content="hello world", model=self.model,
            total_tokens=42, raw={"_cost_usd": 0.001},
        )

    async def ping(self) -> bool:  # pragma: no cover
        return True

    async def list_models(self) -> list[str]:  # pragma: no cover
        return [self.model]


def test_write_trace_appends_jsonl(tmp_path: Path):
    p = tmp_path / "x.jsonl"
    write_trace({"a": 1}, path=p)
    write_trace({"a": 2}, path=p)
    lines = p.read_text(encoding="utf-8").splitlines()
    assert [json.loads(l) for l in lines] == [{"a": 1}, {"a": 2}]


def test_write_trace_silent_on_failure(tmp_path: Path):
    # path inside read-only dir → should not raise
    bad = tmp_path / "no_such_dir" / "deep" / "x.jsonl"
    write_trace({"a": 1}, path=bad)  # creates parent, OK
    assert bad.exists()


def test_tracing_worker_records_success(tmp_path: Path):
    p = tmp_path / "trace.jsonl"
    inner = FakeWorker()
    w = TracingWorker(inner, path=p, run_id="r1", agent_name="A")
    out = asyncio.run(w.chat([{"role": "user", "content": "hi"}]))
    assert out.content == "hello world"
    rec = json.loads(p.read_text().splitlines()[0])
    assert rec["ok"] is True
    assert rec["model"] == "fake-1"
    assert rec["agent"] == "A"
    assert rec["run_id"] == "r1"
    assert rec["tokens"] == 42
    assert rec["output"].startswith("hello")


def test_tracing_worker_records_error(tmp_path: Path):
    p = tmp_path / "trace.jsonl"
    inner = FakeWorker(fail=True)
    w = TracingWorker(inner, path=p)
    with pytest.raises(RuntimeError):
        asyncio.run(w.chat([{"role": "user", "content": "hi"}]))
    rec = json.loads(p.read_text().splitlines()[0])
    assert rec["ok"] is False
    assert "boom" in rec["error"]


def test_tracing_truncates_long_messages(tmp_path: Path):
    p = tmp_path / "trace.jsonl"
    inner = FakeWorker()
    w = TracingWorker(inner, path=p, max_msg_chars=20, max_resp_chars=5)
    big = "x" * 1000
    asyncio.run(w.chat([{"role": "user", "content": big}]))
    rec = json.loads(p.read_text().splitlines()[0])
    assert "truncated" in rec["input"]["msgs"][0]["content"]
    assert "truncated" in rec["output"]
