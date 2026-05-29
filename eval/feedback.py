"""ELO 反哺 capability_store：把离线评测的相对排名写入 models_profile.json。

设计：
- 不直接覆盖 quality.avg_score（那是在线统计），而是写到 verdict 之外的
  独立 namespace `eval_elo`，包含 rating、rank、games、updated_at。
- 也允许把 ELO 顶部的若干模型加到 strengths（领域 = benchmark 名）。
- 这样 model_selector / agent.preferred_models 可以读取这些信号做参考。
"""

from __future__ import annotations

import time
from typing import Any

from eval.elo import EloTable
from src.config import load_models_profile, save_models_profile


def write_elo_to_profiles(
    elo: EloTable,
    *,
    benchmark: str,
    top_strength_count: int = 0,
) -> dict[str, dict[str, Any]]:
    """把 ELO 表写入 models_profile.json 的每个对应模型 entry。

    返回 updated profiles dict（已落盘）。
    """
    profiles = load_models_profile()
    leaderboard = elo.leaderboard()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")

    top_names = {name for name, _r, _g in leaderboard[:top_strength_count]} if top_strength_count > 0 else set()

    for rank, (name, rating, games) in enumerate(leaderboard, start=1):
        p = profiles.get(name)
        if p is None:
            # eval 命中了一个未登记的模型；建一个最小 entry，避免覆盖在线统计
            from src.memory.capability_store import _empty_profile  # local import to avoid cycle
            p = _empty_profile()

        eval_block = p.setdefault("eval_elo", {})
        eval_block.setdefault("benchmarks", {})
        eval_block["benchmarks"][benchmark] = {
            "rating": round(rating, 2),
            "rank": rank,
            "games": games,
            "updated_at": now,
        }
        # also a flat "best" view for quick lookup
        best = eval_block.get("best", {"rating": -1, "benchmark": None})
        if rating > best.get("rating", -1):
            eval_block["best"] = {
                "rating": round(rating, 2),
                "benchmark": benchmark,
                "rank": rank,
                "updated_at": now,
            }

        if name in top_names:
            strengths = set(p.get("strengths", []))
            strengths.add(f"eval:{benchmark}")
            p["strengths"] = sorted(strengths)

        profiles[name] = p

    save_models_profile(profiles)
    return profiles


def read_eval_rank(model: str, benchmark: str) -> int | None:
    """便捷读取：返回模型在某 benchmark 下的 rank（1-based），无则 None。"""
    profiles = load_models_profile()
    p = profiles.get(model) or {}
    return (
        p.get("eval_elo", {})
         .get("benchmarks", {})
         .get(benchmark, {})
         .get("rank")
    )
