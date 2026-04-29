"""Main Orchestrator: state machine driving the full task lifecycle.

States: received → privacy_scan → analysis → planning → executing
        → reviewing → integrating → delivered

The Orchestrator wires together every module:
  PrivacyGuard → TaskPlanner → ModelSelector → Workers → Monitor
  → Integrator → CapabilityStore / TaskHistory / RAG
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Awaitable, Optional

from src.config import (
    AppConfig, DATA_DIR, load_config, load_models_profile,
    load_user_profile, save_user_profile, reload_config,
)
from src.cost.calculator import CostTracker, estimate_cost
from src.leader.integrator import (
    IntegrationResult, ReviewResult, integrate_results, review_subtask,
)
from src.leader.model_selector import assign_models, get_fallback_worker
from src.leader.monitor import (
    MonitorState, SubtaskStatus, init_monitor, monitor_loop,
    write_heartbeat, write_leader_heartbeat,
)
from src.leader.task_planner import Subtask, TaskPlan, plan_task
from src.mcp.server import MCPToolRegistry
from src.memory.capability_store import record_task_result
from src.memory.rag_engine import index_task_result, query as rag_query
from src.memory.user_pref_selector import select_user_preferences
from src.memory.task_history import (
    SubtaskRecord, TaskRecord, save_subtask, save_task,
)
from src.models.api_model import APIModelWorker
from src.models.base import BaseModelWorker, ModelResponse
from src.models.local_model import OllamaWorker
from src.privacy.guard import PrivacyGuard, SanitizeResult


class TaskState(str, Enum):
    RECEIVED = "received"
    PRIVACY_SCAN = "privacy_scan"
    ANALYSIS = "analysis"
    PLANNING = "planning"
    EXECUTING = "executing"
    REVIEWING = "reviewing"
    INTEGRATING = "integrating"
    DELIVERED = "delivered"
    FAILED = "failed"


@dataclass
class SessionSnapshot:
    session_id: str
    original_task: str
    sanitized_task: str
    subtasks: list[dict[str, Any]]
    cost_used_usd: float
    leader_model: str
    state: str
    placeholder_map: dict[str, str] = field(default_factory=dict)

    def save(self) -> Path:
        d = DATA_DIR / "sessions" / self.session_id
        d.mkdir(parents=True, exist_ok=True)
        p = d / "state.json"
        p.write_text(json.dumps(self.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")
        return p

    @classmethod
    def load(cls, session_id: str) -> SessionSnapshot | None:
        p = DATA_DIR / "sessions" / session_id / "state.json"
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        return cls(**data)


# -- Callback type for UI updates --
ProgressCallback = Optional[Callable[[str, dict[str, Any]], Awaitable[None]]]


def _create_leader_worker(cfg: AppConfig) -> BaseModelWorker:
    return OllamaWorker(
        model=cfg.leader.model,
        base_url=cfg.leader.ollama_base_url,
    )


def _create_worker(model: str, cfg: AppConfig) -> BaseModelWorker:
    for w in cfg.workers_api:
        if w.model == model:
            return APIModelWorker(model=model, api_key=w.api_key)
    return OllamaWorker(model=model, base_url=cfg.leader.ollama_base_url)


WORKER_PROMPT = """\
你是一个专业的AI助手，正在执行一个具体的子任务。请认真、完整地完成以下任务。

## 任务
{description}

## 要求
1. 输出高质量、可直接使用的结果
2. 如果是代码任务，输出可运行的完整代码
3. 如果是文本任务，输出结构清晰的内容
"""

USER_PROFILE_UPDATE_PROMPT = """\
你是用户偏好学习助手。请根据最近一次完整任务的上下文，更新用户偏好摘要。

## 现有用户偏好摘要
{current_summary}

## 最近任务
{task_description}

## 最近任务交付结果（截断）
{final_output_excerpt}

## 最近任务质量均分
{quality_avg}

## 输出要求
请输出严格 JSON，不要附加其他文字：
```json
{{
  "natural_language_summary": "更新后的偏好摘要（2-6句，关注可执行偏好）",
  "interaction_history_summary": "本次任务体现出的偏好变化（1-3句）"
}}
```
约束：
1) 不要臆造身份信息或隐私信息
2) 只保留可指导后续任务执行的偏好
3) 若信息不足，可在原摘要基础上小幅优化表达
"""


class Orchestrator:
    """Central state machine driving the full task lifecycle."""

    def __init__(self, cfg: AppConfig | None = None):
        self.cfg = cfg or load_config()
        self.session_id = f"sess_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        self.state = TaskState.RECEIVED
        self.leader = _create_leader_worker(self.cfg)
        self.privacy_guard = PrivacyGuard(entities=self.cfg.privacy.entities)
        self.cost_tracker = CostTracker(budget_usd=self.cfg.cost.monthly_budget_usd)
        self.plan: TaskPlan | None = None
        self.reviews: list[ReviewResult] = []
        self.integration: IntegrationResult | None = None
        self._sanitize_result: SanitizeResult | None = None
        self._original_task: str = ""
        self._on_progress: ProgressCallback = None
        self._mcp = MCPToolRegistry()
        self._runtime_stats: dict[str, dict[str, float]] = {}

    def set_progress_callback(self, cb: ProgressCallback) -> None:
        self._on_progress = cb

    async def _emit(self, event: str, data: dict[str, Any] | None = None) -> None:
        if self._on_progress:
            await self._on_progress(event, data or {})

    def _snapshot(self) -> SessionSnapshot:
        return SessionSnapshot(
            session_id=self.session_id,
            original_task=self._original_task,
            sanitized_task=self._sanitize_result.sanitized if self._sanitize_result else self._original_task,
            subtasks=[s.to_dict() for s in (self.plan.subtasks if self.plan else [])],
            cost_used_usd=self.cost_tracker.spent_usd,
            leader_model=self.cfg.leader.model,
            state=self.state.value,
            placeholder_map=self._sanitize_result.placeholder_map if self._sanitize_result else {},
        )

    async def run(
        self,
        task_description: str,
        *,
        precomputed_plan: TaskPlan | None = None,
    ) -> IntegrationResult:
        """Execute the complete lifecycle for a user task.

        If precomputed_plan is provided, planning output is reused instead of
        generating a brand new plan from the Leader.
        """
        self._original_task = task_description

        try:
            # -- Privacy scan --
            self.state = TaskState.PRIVACY_SCAN
            await self._emit("state", {"state": self.state.value})

            if self.cfg.privacy.enabled:
                self._sanitize_result = self.privacy_guard.sanitize(task_description)
                await self._emit("privacy", {
                    "has_sensitive": self._sanitize_result.has_sensitive,
                    "entity_count": len(self._sanitize_result.spans),
                })
                task_for_workers = self._sanitize_result.sanitized
            else:
                task_for_workers = task_description

            # -- Analysis / Planning --
            self.state = TaskState.ANALYSIS
            await self._emit("state", {"state": self.state.value})

            if not await self.leader.ping():
                raise RuntimeError(
                    f"Ollama 不可用：未连接 {self.cfg.leader.ollama_base_url}，或本地没有模型 "
                    f"{self.cfg.leader.model!r}。\n"
                    f"请先启动: ollama serve\n"
                    f"再拉取: ollama pull {self.cfg.leader.model}\n"
                    f"如显存不足，可改 config/config.yaml 中 leader.model 为较小模型(如 qwen2.5:7b)后重试。"
                )

            profiles = load_models_profile()
            user_profile = load_user_profile()
            user_pref = await select_user_preferences(
                self.leader,
                task_for_workers,
                user_profile,
            )

            rag_results = rag_query(task_for_workers, n_results=3)
            rag_context = "\n".join(r["text"] for r in rag_results) if rag_results else ""

            self.state = TaskState.PLANNING
            await self._emit("state", {"state": self.state.value})

            if precomputed_plan is not None:
                self.plan = precomputed_plan
            else:
                self.plan = await plan_task(
                    self.leader,
                    task_for_workers,
                    worker_profiles=profiles,
                    user_preferences=user_pref,
                    rag_context=rag_context,
                    tool_context=self._mcp.get_tools_description(),
                )

            await self._emit("plan", {"analysis": self.plan.analysis, "subtask_count": len(self.plan.subtasks)})

            # -- Model assignment --
            assign_models(self.plan.subtasks, self.cfg, self.cost_tracker)
            await self._emit("assignment", {
                "assignments": [
                    {"id": s.id, "title": s.title, "model": s.assigned_model}
                    for s in self.plan.subtasks
                ]
            })

            # -- Execution --
            self.state = TaskState.EXECUTING
            await self._emit("state", {"state": self.state.value})

            monitor_state = init_monitor(
                task_id=self.session_id,
                subtask_models={s.id: s.assigned_model for s in self.plan.subtasks},
                timeout_s=self.cfg.monitor.timeout_threshold_s,
                max_retries=self.cfg.monitor.max_retries,
                heartbeat_interval_s=self.cfg.monitor.heartbeat_interval_s,
            )

            # Respect dependency order: execute subtasks with no deps first, then dependents.
            await self._execute_subtasks(self.plan.subtasks, monitor_state)

            # Save snapshot after execution
            self._snapshot().save()

            # -- Review --
            self.state = TaskState.REVIEWING
            await self._emit("state", {"state": self.state.value})

            self.reviews = []
            for st in self.plan.subtasks:
                if st.status == "completed":
                    rev = await review_subtask(self.leader, st)
                    self.reviews.append(rev)
                    await self._emit("review", {
                        "subtask_id": st.id,
                        "score": rev.quality_score,
                        "passed": rev.passed,
                    })

                    # Re-execute if review fails
                    if not rev.passed and st.assigned_model:
                        await self._emit("rework", {"subtask_id": st.id, "reason": rev.suggestions})
                        st.status = "pending"
                        st.result = None
                        await self._execute_single(st, monitor_state)
                        new_rev = await review_subtask(self.leader, st)
                        self.reviews.append(new_rev)

            # -- Integration --
            self.state = TaskState.INTEGRATING
            await self._emit("state", {"state": self.state.value})

            self.integration = await integrate_results(
                self.leader,
                self._original_task,
                self.plan.subtasks,
                self.reviews,
                tool_context=self._mcp.get_tools_description(),
            )

            # Restore sensitive info in final output
            if self._sanitize_result and self._sanitize_result.has_sensitive:
                self.integration.final_output = self.privacy_guard.restore(
                    self.integration.final_output,
                    self._sanitize_result.placeholder_map,
                )

            self.state = TaskState.DELIVERED
            # -- Record to memory (after final state is known) --
            await self._record_to_memory()
            await self._emit("state", {"state": self.state.value, "cost": self.cost_tracker.to_dict()})

            return self.integration

        except Exception as exc:
            self.state = TaskState.FAILED
            await self._emit("error", {"error": str(exc)})
            raise

    async def _execute_subtasks(
        self,
        subtasks: list[Subtask],
        monitor_state: MonitorState,
    ) -> None:
        """Execute subtasks respecting dependency order."""
        completed_ids: set[str] = {s.id for s in subtasks if s.status == "completed"}
        pending = [s for s in subtasks if s.status != "completed"]

        async def _on_timeout(sid: str, _st: SubtaskStatus) -> None:
            await self._emit("subtask_error", {
                "subtask_id": sid,
                "error": "heartbeat timeout detected by monitor",
                "retry": _st.retries,
            })

        monitor_task = asyncio.create_task(monitor_loop(monitor_state, on_timeout=_on_timeout))

        try:
            while pending:
                ready = [
                    s for s in pending
                    if all(dep in completed_ids for dep in s.depends_on)
                ]
                if not ready:
                    if pending:
                        ready = [pending[0]]
                    else:
                        break

                # Launch heartbeat writer for leader
                write_leader_heartbeat(self.session_id)

                tasks = [self._execute_single(s, monitor_state) for s in ready]
                await asyncio.gather(*tasks)

                for s in ready:
                    if s.status == "completed":
                        completed_ids.add(s.id)
                    pending.remove(s)
        finally:
            monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await monitor_task

    async def _execute_single(
        self,
        subtask: Subtask,
        monitor_state: MonitorState,
    ) -> None:
        """Execute a single subtask with heartbeat and failover."""
        model_name = subtask.assigned_model
        if not model_name:
            subtask.status = "failed"
            return

        ms = monitor_state.statuses.get(subtask.id)
        if ms:
            ms.status = "running"
            ms.last_heartbeat = time.time()

        worker = _create_worker(model_name, self.cfg)
        prompt = WORKER_PROMPT.format(description=subtask.description)

        retries = 0
        max_retries = self.cfg.monitor.max_retries

        while retries <= max_retries:
            try:
                stop_heartbeat = asyncio.Event()

                async def _heartbeat_loop() -> None:
                    while not stop_heartbeat.is_set():
                        write_heartbeat(self.session_id, subtask.id, {"model": model_name, "status": "running"})
                        await asyncio.sleep(max(self.cfg.monitor.heartbeat_interval_s / 2, 1))

                heartbeat_task = asyncio.create_task(_heartbeat_loop())
                await self._emit("subtask_start", {
                    "subtask_id": subtask.id, "model": model_name, "retry": retries,
                })

                resp = await asyncio.wait_for(
                    worker.chat(messages=[{"role": "user", "content": prompt}]),
                    timeout=self.cfg.monitor.timeout_threshold_s,
                )
                stop_heartbeat.set()
                with contextlib.suppress(asyncio.CancelledError):
                    heartbeat_task.cancel()
                    await heartbeat_task

                write_heartbeat(self.session_id, subtask.id, {"status": "completed"})
                subtask.result = resp.content
                subtask.status = "completed"
                self._runtime_stats[subtask.id] = {
                    "tokens_used": float(resp.total_tokens),
                    "elapsed_s": float(resp.elapsed_s),
                    "cost_usd": float(resp.cost_usd or estimate_cost(resp.prompt_tokens, resp.completion_tokens, model_name)),
                }

                cost = resp.cost_usd or estimate_cost(resp.prompt_tokens, resp.completion_tokens, model_name)
                self.cost_tracker.record(resp.prompt_tokens, resp.completion_tokens, cost)

                if ms:
                    ms.status = "completed"
                    ms.result = resp.content

                await self._emit("subtask_done", {
                    "subtask_id": subtask.id,
                    "model": model_name,
                    "tokens": resp.total_tokens,
                    "cost": cost,
                    "elapsed_s": resp.elapsed_s,
                })
                return

            except (asyncio.TimeoutError, Exception) as exc:
                if 'stop_heartbeat' in locals():
                    stop_heartbeat.set()
                if 'heartbeat_task' in locals():
                    with contextlib.suppress(asyncio.CancelledError):
                        heartbeat_task.cancel()
                        await heartbeat_task
                retries += 1
                await self._emit("subtask_error", {
                    "subtask_id": subtask.id, "model": model_name,
                    "error": str(exc), "retry": retries,
                })

                if retries > max_retries:
                    break

                # Failover
                fb = get_fallback_worker(model_name, subtask, self.cfg)
                if fb:
                    model_name = fb.model
                    worker = _create_worker(model_name, self.cfg)
                    subtask.assigned_model = model_name
                    await self._emit("failover", {
                        "subtask_id": subtask.id, "new_model": model_name,
                    })

        subtask.status = "failed"
        if ms:
            ms.status = "failed"

    async def _record_to_memory(self) -> None:
        """Persist results to capability store, task history, and RAG."""
        task_record = TaskRecord(
            id=self.session_id,
            description=self._original_task[:500],
            status="completed" if self.state == TaskState.DELIVERED else "failed",
            completed_at=datetime.datetime.utcnow(),
            total_cost_usd=self.cost_tracker.spent_usd,
            leader_model=self.cfg.leader.model,
        )
        save_task(task_record)

        for st in (self.plan.subtasks if self.plan else []):
            rev = next((r for r in self.reviews if r.subtask_id == st.id), None)
            quality = rev.quality_score if rev else 5.0
            passed = rev.passed if rev else True

            sub_record = SubtaskRecord(
                id=st.id,
                task_id=self.session_id,
                description=st.description[:500],
                assigned_model=st.assigned_model,
                status=st.status,
                quality_score=quality,
                tokens_used=int(self._runtime_stats.get(st.id, {}).get("tokens_used", st.estimated_tokens)),
                elapsed_s=float(self._runtime_stats.get(st.id, {}).get("elapsed_s", 0.0)),
                cost_usd=float(self._runtime_stats.get(st.id, {}).get("cost_usd", 0.0)),
                passed_review=1 if passed else 0,
                result_summary=(st.result or "")[:500],
                completed_at=datetime.datetime.utcnow() if st.status == "completed" else None,
            )
            save_subtask(sub_record)

            if st.assigned_model and st.status in ("completed", "failed"):
                is_local = any(w.model == st.assigned_model for w in self.cfg.workers_local)
                record_task_result(
                    st.assigned_model,
                    quality_score=quality,
                    elapsed_s=float(self._runtime_stats.get(st.id, {}).get("elapsed_s", 0.0)),
                    tokens_used=int(self._runtime_stats.get(st.id, {}).get("tokens_used", st.estimated_tokens)),
                    cost_usd=0.0 if is_local else float(
                        self._runtime_stats.get(st.id, {}).get(
                            "cost_usd",
                            estimate_cost(st.estimated_tokens, st.estimated_tokens, st.assigned_model),
                        )
                    ),
                    passed_review=passed,
                    failed=(st.status == "failed"),
                    is_local=is_local,
                )

            if st.status == "completed" and st.result:
                index_task_result(st.id, st.description, st.result[:300], st.assigned_model or "unknown")

        await self._auto_update_user_profile()

    async def _auto_update_user_profile(self) -> None:
        """Use Leader to auto-summarize user preferences after each completed task."""
        if not self.integration or self.state != TaskState.DELIVERED:
            return

        profile = load_user_profile()
        current_summary = profile.get("natural_language_summary", "") or "（空）"
        final_output_excerpt = (self.integration.final_output or "")[:1800]
        prompt = USER_PROFILE_UPDATE_PROMPT.format(
            current_summary=current_summary,
            task_description=self._original_task[:1200],
            final_output_excerpt=final_output_excerpt,
            quality_avg=self.integration.total_quality_avg,
        )

        try:
            resp = await self.leader.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=700,
            )
            data = json.loads(resp.content.strip().split("```json")[-1].split("```")[0].strip()) \
                if "```" in resp.content else json.loads(resp.content.strip())
        except Exception:
            return

        new_summary = str(data.get("natural_language_summary", "")).strip()
        history_summary = str(data.get("interaction_history_summary", "")).strip()
        if not new_summary:
            return

        profile["natural_language_summary"] = new_summary[:4000]
        if history_summary:
            profile["interaction_history_summary"] = history_summary[:2000]
        profile["last_updated"] = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        profile["update_count"] = int(profile.get("update_count", 0)) + 1
        save_user_profile(profile)

    async def switch_leader(self, new_model: str, persist: bool = False) -> None:
        """Hot-switch the Leader model mid-session."""
        self._snapshot().save()
        self.cfg.leader.model = new_model
        self.leader = _create_leader_worker(self.cfg)
        if persist:
            from src.config import save_config
            save_config(self.cfg)
        await self._emit("leader_switch", {"new_model": new_model, "persisted": persist})

    @classmethod
    def from_snapshot(cls, session_id: str, cfg: AppConfig | None = None) -> "Orchestrator":
        """Rebuild an orchestrator from saved session snapshot."""
        snap = SessionSnapshot.load(session_id)
        if not snap:
            raise ValueError(f"Session snapshot not found: {session_id}")

        orch = cls(cfg=cfg)
        orch.session_id = snap.session_id
        orch._original_task = snap.original_task
        orch.state = TaskState(snap.state) if snap.state in TaskState._value2member_map_ else TaskState.RECEIVED
        orch.cost_tracker.spent_usd = snap.cost_used_usd

        if snap.placeholder_map:
            orch._sanitize_result = SanitizeResult(
                original=snap.original_task,
                sanitized=snap.sanitized_task,
                spans=[],
                placeholder_map=snap.placeholder_map,
                has_sensitive=True,
            )

        subtasks: list[Subtask] = []
        for s in snap.subtasks:
            subtasks.append(Subtask(
                id=s.get("id", ""),
                title=s.get("title", "未命名子任务"),
                description=s.get("description", ""),
                importance=int(s.get("importance", 5)),
                required_skills=s.get("required_skills", []),
                depends_on=s.get("depends_on", []),
                estimated_tokens=int(s.get("estimated_tokens", 2000)),
                assigned_model=s.get("assigned_model"),
                status=s.get("status", "pending"),
                result=s.get("result"),
            ))
        orch.plan = TaskPlan(analysis="Resumed from saved snapshot", subtasks=subtasks, raw_response="")
        return orch

    async def resume(self) -> IntegrationResult:
        """Resume current session from restored state."""
        if not self.plan:
            raise RuntimeError("No task plan in snapshot; cannot resume")

        self.state = TaskState.EXECUTING
        await self._emit("state", {"state": self.state.value, "resumed": True})
        monitor_state = init_monitor(
            task_id=self.session_id,
            subtask_models={s.id: s.assigned_model or "unknown" for s in self.plan.subtasks},
            timeout_s=self.cfg.monitor.timeout_threshold_s,
            max_retries=self.cfg.monitor.max_retries,
            heartbeat_interval_s=self.cfg.monitor.heartbeat_interval_s,
        )
        await self._execute_subtasks(self.plan.subtasks, monitor_state)

        self.state = TaskState.REVIEWING
        await self._emit("state", {"state": self.state.value})
        self.reviews = []
        for st in self.plan.subtasks:
            if st.status == "completed":
                rev = await review_subtask(self.leader, st)
                self.reviews.append(rev)
                await self._emit("review", {"subtask_id": st.id, "score": rev.quality_score, "passed": rev.passed})

        self.state = TaskState.INTEGRATING
        await self._emit("state", {"state": self.state.value})
        self.integration = await integrate_results(
            self.leader,
            self._original_task,
            self.plan.subtasks,
            self.reviews,
            tool_context=self._mcp.get_tools_description(),
        )
        if self._sanitize_result and self._sanitize_result.has_sensitive:
            self.integration.final_output = self.privacy_guard.restore(
                self.integration.final_output,
                self._sanitize_result.placeholder_map,
            )
        self.state = TaskState.DELIVERED
        await self._record_to_memory()
        await self._emit("state", {"state": self.state.value, "cost": self.cost_tracker.to_dict()})
        return self.integration
