"""Tests for the monitoring and heartbeat system."""

import time
import tempfile
from pathlib import Path

import pytest

from src.leader.monitor import (
    MonitorState,
    SubtaskStatus,
    init_monitor,
    write_heartbeat,
    read_heartbeat,
    refresh_heartbeats,
    write_leader_heartbeat,
    check_leader_alive,
)


@pytest.fixture(autouse=True)
def setup_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr("src.leader.monitor.HEARTBEAT_DIR", tmp_path / "tasks")
    monkeypatch.setattr("src.leader.monitor.DATA_DIR", tmp_path)
    yield


def test_write_and_read_heartbeat():
    write_heartbeat("task1", "sub1", {"model": "test"})
    ts = read_heartbeat("task1", "sub1")
    assert ts > 0
    assert time.time() - ts < 5


def test_monitor_detects_timeout():
    state = init_monitor(
        task_id="t1",
        subtask_models={"s1": "model_a"},
        timeout_s=0.1,
    )
    state.statuses["s1"].status = "running"
    state.statuses["s1"].last_heartbeat = time.time() - 10

    timed_out = refresh_heartbeats(state)
    assert "s1" in timed_out


def test_monitor_no_timeout_when_fresh():
    state = init_monitor(
        task_id="t2",
        subtask_models={"s1": "model_a"},
        timeout_s=60,
    )
    state.statuses["s1"].status = "running"
    write_heartbeat("t2", "s1")
    state.statuses["s1"].last_heartbeat = time.time()

    timed_out = refresh_heartbeats(state)
    assert "s1" not in timed_out


def test_leader_heartbeat():
    write_leader_heartbeat("sess_test")
    assert check_leader_alive(watchdog_timeout_s=30)


def test_leader_heartbeat_expired(tmp_path, monkeypatch):
    monkeypatch.setattr("src.leader.monitor.DATA_DIR", tmp_path)
    hb = tmp_path / "sessions" / "leader_heartbeat"
    hb.parent.mkdir(parents=True, exist_ok=True)
    hb.write_text('{"ts": 0}')
    import os
    os.utime(hb, (0, 0))
    assert not check_leader_alive(watchdog_timeout_s=1)
