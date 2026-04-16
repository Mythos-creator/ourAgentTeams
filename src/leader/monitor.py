"""Progress Monitor: heartbeat files, timeout detection, and failover logic.

Each running subtask writes a heartbeat file; the monitor polls them.
When a heartbeat goes stale beyond the threshold, the subtask is
reassigned to a fallback model.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from src.config import DATA_DIR


HEARTBEAT_DIR = DATA_DIR / "tasks"


@dataclass
class SubtaskStatus:
    subtask_id: str
    model: str
    status: str = "pending"      # pending | running | completed | failed | timeout
    heartbeat_path: Path | None = None
    last_heartbeat: float = 0.0
    retries: int = 0
    result: str | None = None
    error: str | None = None


@dataclass
class MonitorState:
    task_id: str
    statuses: dict[str, SubtaskStatus] = field(default_factory=dict)
    timeout_threshold_s: float = 120.0
    max_retries: int = 3
    heartbeat_interval_s: float = 10.0

    @property
    def all_done(self) -> bool:
        return all(
            s.status in ("completed", "failed")
            for s in self.statuses.values()
        )

    @property
    def running_ids(self) -> list[str]:
        return [sid for sid, s in self.statuses.items() if s.status == "running"]

    @property
    def timed_out_ids(self) -> list[str]:
        now = time.time()
        return [
            sid for sid, s in self.statuses.items()
            if s.status == "running"
            and s.last_heartbeat > 0
            and (now - s.last_heartbeat) > self.timeout_threshold_s
        ]


def _heartbeat_path(task_id: str, subtask_id: str) -> Path:
    d = HEARTBEAT_DIR / task_id
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{subtask_id}.heartbeat"


def write_heartbeat(task_id: str, subtask_id: str, payload: dict[str, Any] | None = None) -> None:
    """Worker calls this periodically to signal liveness."""
    p = _heartbeat_path(task_id, subtask_id)
    data = {"ts": time.time(), "subtask_id": subtask_id, **(payload or {})}
    p.write_text(json.dumps(data), encoding="utf-8")


def read_heartbeat(task_id: str, subtask_id: str) -> float:
    """Return the last heartbeat timestamp, or 0 if not found."""
    p = _heartbeat_path(task_id, subtask_id)
    if not p.exists():
        return 0.0
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def write_leader_heartbeat(session_id: str) -> None:
    """Leader-level heartbeat for the Watchdog to monitor."""
    d = DATA_DIR / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "leader_heartbeat"
    p.write_text(json.dumps({"ts": time.time(), "session": session_id}), encoding="utf-8")


def check_leader_alive(watchdog_timeout_s: float = 30.0) -> bool:
    p = DATA_DIR / "sessions" / "leader_heartbeat"
    if not p.exists():
        return False
    try:
        return (time.time() - p.stat().st_mtime) < watchdog_timeout_s
    except OSError:
        return False


def init_monitor(
    task_id: str,
    subtask_models: dict[str, str],
    timeout_s: float = 120.0,
    max_retries: int = 3,
    heartbeat_interval_s: float = 10.0,
) -> MonitorState:
    """Create a fresh MonitorState for a task."""
    statuses = {}
    for sid, model in subtask_models.items():
        hb_path = _heartbeat_path(task_id, sid)
        statuses[sid] = SubtaskStatus(
            subtask_id=sid,
            model=model,
            heartbeat_path=hb_path,
        )
    return MonitorState(
        task_id=task_id,
        statuses=statuses,
        timeout_threshold_s=timeout_s,
        max_retries=max_retries,
        heartbeat_interval_s=heartbeat_interval_s,
    )


def refresh_heartbeats(state: MonitorState) -> list[str]:
    """Check heartbeat files and return list of timed-out subtask ids."""
    for sid, st in state.statuses.items():
        if st.status != "running":
            continue
        mtime = read_heartbeat(state.task_id, sid)
        if mtime > 0:
            st.last_heartbeat = mtime

    return state.timed_out_ids


async def monitor_loop(
    state: MonitorState,
    on_timeout: Callable[[str, SubtaskStatus], Awaitable[None]] | None = None,
    on_complete: Callable[[], Awaitable[None]] | None = None,
    poll_interval: float | None = None,
) -> None:
    """Async loop that polls heartbeats until all subtasks finish."""
    interval = poll_interval or state.heartbeat_interval_s

    while not state.all_done:
        timed_out = refresh_heartbeats(state)

        for sid in timed_out:
            st = state.statuses[sid]
            if st.retries >= state.max_retries:
                st.status = "failed"
                st.error = f"Exceeded {state.max_retries} retries after timeout"
            else:
                st.status = "timeout"
                st.retries += 1
                if on_timeout:
                    await on_timeout(sid, st)

        await asyncio.sleep(interval)

    if on_complete:
        await on_complete()
