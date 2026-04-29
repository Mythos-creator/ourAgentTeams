"""Model Selector: match subtasks to workers based on capability + cost routing.

Combines the capability profile, cost tier logic, and budget constraints to
decide which model handles each subtask.
"""

from __future__ import annotations

from typing import Any

from src.config import AppConfig, WorkerEntry
from src.cost.calculator import CostTracker, pick_tier
from src.memory.capability_store import (
    VERDICT_NOT_WORTH_PAYING,
    VERDICT_CONSIDER_REPLACING,
    get_profile,
)
from src.leader.task_planner import Subtask


def _skill_overlap(required: list[str], strengths: list[str]) -> int:
    """Count how many required skills appear in the model's strengths."""
    req = {s.lower() for s in required}
    st = {s.lower() for s in strengths}
    return len(req & st)


def _quality_score(model: str) -> float:
    p = get_profile(model)
    return p.get("performance", {}).get("quality", {}).get("avg_score", 5.0)


def _is_disqualified(model: str) -> bool:
    """Models marked not_worth_paying or consider_replacing are deprioritized."""
    p = get_profile(model)
    v = p.get("verdict", {}).get("status", "")
    return v in (VERDICT_NOT_WORTH_PAYING,)


def select_model_for_subtask(
    subtask: Subtask,
    cfg: AppConfig,
    cost_tracker: CostTracker,
) -> WorkerEntry:
    """Pick the best worker for a subtask given importance + capability + budget."""
    tier = pick_tier(
        subtask.importance,
        threshold_local=cfg.cost.importance_threshold_local,
        threshold_best=cfg.cost.importance_threshold_best,
    )

    if cost_tracker.over_budget:
        tier = "local"

    candidates: list[tuple[WorkerEntry, float]] = []

    if tier == "local":
        for w in cfg.workers_local:
            score = _skill_overlap(subtask.required_skills, w.strengths) + 1.0
            candidates.append((w, score))
    elif tier == "best":
        for w in cfg.workers_api:
            if _is_disqualified(w.model):
                continue
            overlap = _skill_overlap(subtask.required_skills, w.strengths)
            q = _quality_score(w.model)
            score = overlap * 2 + q
            candidates.append((w, score))
        for w in cfg.workers_local:
            overlap = _skill_overlap(subtask.required_skills, w.strengths)
            q = _quality_score(w.model)
            candidates.append((w, overlap * 1.0 + q * 0.5))
    else:  # mid
        for w in cfg.workers_api:
            if _is_disqualified(w.model):
                continue
            overlap = _skill_overlap(subtask.required_skills, w.strengths)
            q = _quality_score(w.model)
            cost_info = get_profile(w.model).get("performance", {}).get("cost", {})
            cpp = cost_info.get("cost_per_quality_point", 0.01)
            efficiency = q / max(cpp, 0.001)
            score = overlap * 2 + efficiency * 0.1
            candidates.append((w, score))
        for w in cfg.workers_local:
            overlap = _skill_overlap(subtask.required_skills, w.strengths)
            q = _quality_score(w.model)
            candidates.append((w, overlap * 1.2 + q * 0.8))

    if not candidates:
        if cfg.workers_local:
            return cfg.workers_local[0]
        if cfg.workers_api:
            return cfg.workers_api[0]
        raise RuntimeError("No workers available")

    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]


def assign_models(
    subtasks: list[Subtask],
    cfg: AppConfig,
    cost_tracker: CostTracker,
) -> list[Subtask]:
    """Assign a model to every subtask in the plan."""
    for st in subtasks:
        worker = select_model_for_subtask(st, cfg, cost_tracker)
        st.assigned_model = worker.model
    return subtasks


def get_fallback_worker(
    current_model: str,
    subtask: Subtask,
    cfg: AppConfig,
) -> WorkerEntry | None:
    """Return next-best worker when the current one fails or times out."""
    all_workers = cfg.workers_api + cfg.workers_local
    remaining = [w for w in all_workers if w.model != current_model]

    if not remaining:
        return None

    scored = []
    for w in remaining:
        if _is_disqualified(w.model):
            continue
        overlap = _skill_overlap(subtask.required_skills, w.strengths)
        q = _quality_score(w.model)
        scored.append((w, overlap * 2 + q))

    if not scored:
        return remaining[0] if remaining else None

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[0][0]
