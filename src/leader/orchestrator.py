"""Main Orchestrator: state machine driving the full task lifecycle.

States: received → privacy_scan → analysis → planning → executing
        → reviewing → integrating → delivered

The Orchestrator wires together every module:
  PrivacyGuard → TaskPlanner → ModelSelector → Workers → Monitor
  → Integrator → CapabilityStore / TaskHistory / RAG
"""

from __future__ import annotations

import asyncio
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
    MonitorState, SubtaskStatus, init_monitor,
    refresh_heartbeats, write_heartbeat, write_leader_heartbeat,
)
from src.leader.task_planner import Subtask, TaskPlan, plan_task
from src.memory.capability_store import record_task_result
from src.memory.rag_engine import index_task_result, query as rag_query
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

    async def run(self, task_description: str) -> IntegrationResult:
        """Execute the complete lifecycle for a user task."""
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

            profiles = load_models_profile()
            user_profile = load_user_profile()
            user_pref = user_profile.get("natural_language_summary", "")

            rag_results = rag_query(task_for_workers, n_results=3)
            rag_context = "\n".join(r["text"] for r in rag_results) if rag_results else ""

            self.state = TaskState.PLANNING
            await self._emit("state", {"state": self.state.value})

            self.plan = await plan_task(
                self.leader,
                task_for_workers,
                worker_profiles=profiles,
                user_preferences=user_pref,
                rag_context=rag_context,
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
            )

            # Restore sensitive info in final output
            if self._sanitize_result and self._sanitize_result.has_sensitive:
                self.integration.final_output = self.privacy_guard.restore(
                    self.integration.final_output,
                    self._sanitize_result.placeholder_map,
                )

            # -- Record to memory --
            await self._record_to_memory()

            self.state = TaskState.DELIVERED
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
        completed_ids: set[str] = set()
        pending = list(subtasks)

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
                completed_ids.add(s.id)
                pending.remove(s)

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
                write_heartbeat(self.session_id, subtask.id, {"model": model_name})
                await self._emit("subtask_start", {
                    "subtask_id": subtask.id, "model": model_name, "retry": retries,
                })

                resp = await asyncio.wait_for(
                    worker.chat(messages=[{"role": "user", "content": prompt}]),
                    timeout=self.cfg.monitor.timeout_threshold_s,
                )

                write_heartbeat(self.session_id, subtask.id, {"status": "completed"})
                subtask.result = resp.content
                subtask.status = "completed"

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
                tokens_used=st.estimated_tokens,
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
                    elapsed_s=0.0,
                    tokens_used=st.estimated_tokens,
                    cost_usd=0.0 if is_local else estimate_cost(st.estimated_tokens, st.estimated_tokens, st.assigned_model),
                    passed_review=passed,
                    failed=(st.status == "failed"),
                    is_local=is_local,
                )

            if st.status == "completed" and st.result:
                index_task_result(st.id, st.description, st.result[:300], st.assigned_model or "unknown")

    async def switch_leader(self, new_model: str, persist: bool = False) -> None:
        """Hot-switch the Leader model mid-session."""
        self._snapshot().save()
        self.cfg.leader.model = new_model
        self.leader = _create_leader_worker(self.cfg)
        if persist:
            from src.config import save_config
            save_config(self.cfg)
        await self._emit("leader_switch", {"new_model": new_model, "persisted": persist})
