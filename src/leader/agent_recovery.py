"""AgentPool 错误恢复辅助：

- record_outcome: 在 run() 之后调用一次，更新错误计数 / 状态。
  连续错误 ≥ error_threshold 转 ERROR；偶发错误进入 COOLDOWN（带过期时间）。
- recover_expired: 周期性扫描，把冷却到期且健康的实例放回 IDLE。
- AutoRecoverer: 异步后台任务，定期调用 recover_expired。

之所以做成独立模块、而不是直接修改 AgentPool/AgentInstance，是因为
agent_pool/agent_instance 已经在被项目其它部分使用，并且本模块希望保持
"可选附加层"的位置。调用方可以选择是否启用。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from .agent_instance import AgentInstance, AgentState
from .agent_pool import AgentPool


@dataclass
class RecoveryPolicy:
    """每个实例独立的错误/恢复策略。"""

    error_threshold: int = 3            # 连续错误达到这个数 → ERROR
    cooldown_seconds: float = 30.0      # 偶发错误后的冷却时长
    max_cooldown_seconds: float = 300.0 # 指数退避上限
    cooldown_backoff: float = 2.0       # 每次进入 cooldown 翻倍


@dataclass
class _RecoveryState:
    consecutive_errors: int = 0
    cooldown_until: float = 0.0
    cooldown_streak: int = 0  # 连续被打入 cooldown 的次数（控制指数退避）
    last_error: str | None = None


class AgentRecoveryManager:
    """对一组 AgentInstance 做"健康度记账 + 自动复活"。"""

    def __init__(self, pool: AgentPool, *, policy: RecoveryPolicy | None = None) -> None:
        self.pool = pool
        self.policy = policy or RecoveryPolicy()
        self._states: dict[str, _RecoveryState] = {}

    def _state(self, instance: AgentInstance) -> _RecoveryState:
        return self._states.setdefault(instance.instance_id, _RecoveryState())

    # ── 事件接入 ──────────────────────────────────────────────────────

    def record_outcome(
        self,
        instance: AgentInstance,
        *,
        success: bool,
        error: BaseException | None = None,
    ) -> None:
        s = self._state(instance)
        if success:
            s.consecutive_errors = 0
            s.cooldown_streak = 0
            s.last_error = None
            # 成功时若处于 cooldown，回到 idle
            if instance.state == AgentState.COOLDOWN:
                instance.mark_idle()
            return

        # failure path
        s.consecutive_errors += 1
        s.last_error = f"{type(error).__name__}: {error}" if error is not None else "unknown"

        if s.consecutive_errors >= self.policy.error_threshold:
            instance.mark_error()
            return

        # transient: cooldown with exponential backoff
        s.cooldown_streak += 1
        delay = min(
            self.policy.cooldown_seconds * (self.policy.cooldown_backoff ** (s.cooldown_streak - 1)),
            self.policy.max_cooldown_seconds,
        )
        s.cooldown_until = time.time() + delay
        instance.mark_cooldown()

    # ── 周期扫描 ──────────────────────────────────────────────────────

    def recover_expired(self, *, now: float | None = None) -> list[str]:
        """把冷却到期、且未达 ERROR 阈值的实例恢复为 IDLE。

        返回被恢复的 instance_id 列表。
        """
        now = now or time.time()
        recovered: list[str] = []
        for ins in self.pool.all():
            if ins.state != AgentState.COOLDOWN:
                continue
            s = self._state(ins)
            if now >= s.cooldown_until:
                ins.mark_idle()
                recovered.append(ins.instance_id)
        return recovered

    def manually_revive(self, instance: AgentInstance) -> None:
        """从 ERROR 中手动恢复（运维入口）。重置错误计数。"""
        s = self._state(instance)
        s.consecutive_errors = 0
        s.cooldown_streak = 0
        s.cooldown_until = 0.0
        s.last_error = None
        if instance.state in (AgentState.ERROR, AgentState.COOLDOWN):
            instance.mark_idle()

    def health_snapshot(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for ins in self.pool.all():
            s = self._state(ins)
            rows.append({
                "instance_id": ins.instance_id,
                "name": ins.name,
                "state": ins.state.value,
                "consecutive_errors": s.consecutive_errors,
                "cooldown_until": s.cooldown_until,
                "last_error": s.last_error,
            })
        return rows


class AutoRecoverer:
    """后台 task：周期调用 RecoveryManager.recover_expired。"""

    def __init__(self, manager: AgentRecoveryManager, *, interval_s: float = 5.0) -> None:
        self.manager = manager
        self.interval_s = interval_s
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.manager.recover_expired()
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_s)
            except asyncio.TimeoutError:
                continue

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await self._task
            except Exception:
                pass
            self._task = None
