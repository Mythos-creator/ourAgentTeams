"""Model capability & performance memory backed by models_profile.json.

After each subtask, the Leader scores the worker model and this module
updates the rolling averages, trend, verdict, etc.
"""

from __future__ import annotations

import time
from typing import Any

from src.config import load_models_profile, save_models_profile

VERDICT_RECOMMENDED = "recommended"
VERDICT_USABLE = "usable"
VERDICT_DECLINING = "declining"
VERDICT_CONSIDER_REPLACING = "consider_replacing"
VERDICT_NOT_WORTH_PAYING = "not_worth_paying"

_RECENT_WINDOW = 5


def _empty_profile() -> dict:
    return {
        "strengths": [],
        "preferred_task_types": [],
        "performance": {
            "total_tasks": 0,
            "completed": 0,
            "failed": 0,
            "timeout": 0,
            "failure_rate": 0.0,
            "quality": {"avg_score": 0.0, "score_trend": "unknown", "recent_scores": []},
            "speed": {"avg_response_time_s": 0.0, "p95_response_time_s": 0.0, "timeout_count": 0},
            "cost": {"total_tokens_used": 0, "total_cost_usd": 0.0, "cost_per_quality_point": 0.0},
            "review_pass_rate": 0.0,
            "rework_count": 0,
        },
        "verdict": {"status": "usable", "reason": "", "last_evaluated": ""},
    }


def get_profile(model: str) -> dict:
    profiles = load_models_profile()
    return profiles.get(model, _empty_profile())


def record_task_result(
    model: str,
    *,
    quality_score: float,
    elapsed_s: float,
    tokens_used: int,
    cost_usd: float,
    passed_review: bool,
    failed: bool = False,
    timed_out: bool = False,
    strengths: list[str] | None = None,
    is_local: bool = False,
) -> dict:
    """Record a single subtask result and recalculate rolling stats."""
    profiles = load_models_profile()
    p = profiles.get(model, _empty_profile())

    if strengths:
        existing = set(p.get("strengths", []))
        existing.update(strengths)
        p["strengths"] = sorted(existing)

    perf = p["performance"]
    perf["total_tasks"] = perf.get("total_tasks", 0) + 1

    if failed:
        perf["failed"] = perf.get("failed", 0) + 1
    elif timed_out:
        perf["timeout"] = perf.get("timeout", 0) + 1
    else:
        perf["completed"] = perf.get("completed", 0) + 1

    total = perf["total_tasks"]
    perf["failure_rate"] = round((perf.get("failed", 0) + perf.get("timeout", 0)) / max(total, 1), 3)

    # Quality rolling average + recent window
    q = perf["quality"]
    recent = q.get("recent_scores", [])
    recent.append(round(quality_score, 1))
    if len(recent) > _RECENT_WINDOW:
        recent = recent[-_RECENT_WINDOW:]
    q["recent_scores"] = recent
    completed = perf.get("completed", 0)
    old_avg = q.get("avg_score", 0.0)
    q["avg_score"] = round(((old_avg * max(completed - 1, 0)) + quality_score) / max(completed, 1), 2)

    if len(recent) >= 3:
        if all(recent[i] <= recent[i - 1] for i in range(1, len(recent))):
            q["score_trend"] = "declining"
        elif all(recent[i] >= recent[i - 1] for i in range(1, len(recent))):
            q["score_trend"] = "improving"
        else:
            q["score_trend"] = "stable"

    # Speed
    s = perf["speed"]
    old_avg_t = s.get("avg_response_time_s", 0.0)
    s["avg_response_time_s"] = round(((old_avg_t * max(total - 1, 0)) + elapsed_s) / max(total, 1), 1)
    s["p95_response_time_s"] = max(s.get("p95_response_time_s", 0.0), elapsed_s)
    if timed_out:
        s["timeout_count"] = s.get("timeout_count", 0) + 1

    # Cost
    c = perf["cost"]
    c["total_tokens_used"] = c.get("total_tokens_used", 0) + tokens_used
    c["total_cost_usd"] = round(c.get("total_cost_usd", 0.0) + cost_usd, 6)
    avg_q = q["avg_score"]
    c["cost_per_quality_point"] = round(c["total_cost_usd"] / max(avg_q * total, 1), 4) if avg_q > 0 else 0.0

    # Review
    if not failed and not timed_out:
        old_pass = perf.get("review_pass_rate", 0.0) * max(completed - 1, 0)
        perf["review_pass_rate"] = round((old_pass + (1.0 if passed_review else 0.0)) / max(completed, 1), 3)
        if not passed_review:
            perf["rework_count"] = perf.get("rework_count", 0) + 1

    # Verdict
    v = p["verdict"]
    v["last_evaluated"] = time.strftime("%Y-%m-%d")
    v.update(_compute_verdict(perf, is_local=is_local))

    profiles[model] = p
    save_models_profile(profiles)
    return p


def _compute_verdict(perf: dict, *, is_local: bool) -> dict[str, str]:
    q = perf["quality"]
    avg = q.get("avg_score", 0.0)
    trend = q.get("score_trend", "unknown")
    fail_rate = perf.get("failure_rate", 0.0)
    pass_rate = perf.get("review_pass_rate", 0.0)
    rework = perf.get("rework_count", 0)
    total = perf.get("total_tasks", 0)

    rework_rate = rework / max(total, 1)

    if not is_local and avg < 5.0 and total >= 5:
        return {"status": VERDICT_NOT_WORTH_PAYING, "reason": "质量长期低于合格线，不值得继续付费"}
    if fail_rate > 0.15 or rework_rate > 0.30:
        return {"status": VERDICT_CONSIDER_REPLACING, "reason": f"失败率{fail_rate:.0%}或返工率{rework_rate:.0%}过高"}
    if trend == "declining":
        return {"status": VERDICT_DECLINING, "reason": "近期质量评分呈下降趋势，需关注"}
    if avg >= 8.0 and fail_rate < 0.05 and pass_rate > 0.85:
        return {"status": VERDICT_RECOMMENDED, "reason": "质量稳定、失败率低、Review通过率高"}
    return {"status": VERDICT_USABLE, "reason": "表现正常，可继续使用"}


def get_all_verdicts() -> dict[str, dict]:
    profiles = load_models_profile()
    return {model: p.get("verdict", {}) for model, p in profiles.items()}


def get_savings_suggestions(profiles: dict | None = None) -> list[dict[str, Any]]:
    """Compare paid models against local ones; suggest cost-saving removals."""
    profiles = profiles or load_models_profile()
    suggestions: list[dict[str, Any]] = []

    local_best_avg = 0.0
    for _model, p in profiles.items():
        if p.get("performance", {}).get("cost", {}).get("total_cost_usd", 0.0) == 0.0:
            avg = p.get("performance", {}).get("quality", {}).get("avg_score", 0.0)
            local_best_avg = max(local_best_avg, avg)

    for model, p in profiles.items():
        cost_info = p.get("performance", {}).get("cost", {})
        total_cost = cost_info.get("total_cost_usd", 0.0)
        if total_cost == 0.0:
            continue
        avg = p.get("performance", {}).get("quality", {}).get("avg_score", 0.0)
        verdict = p.get("verdict", {}).get("status", "")

        if avg <= local_best_avg and verdict in (VERDICT_NOT_WORTH_PAYING, VERDICT_CONSIDER_REPLACING):
            suggestions.append({
                "model": model,
                "action": "停用",
                "reason": f"质量({avg:.1f})不优于本地模型({local_best_avg:.1f})，已花费${total_cost:.2f}",
                "estimated_monthly_savings_usd": round(total_cost, 2),
            })
        elif verdict == VERDICT_DECLINING:
            suggestions.append({
                "model": model,
                "action": "降级为备用",
                "reason": "近期质量下滑，建议仅作备选",
                "estimated_monthly_savings_usd": round(total_cost * 0.6, 2),
            })

    return suggestions
