"""月度预算守门：根据当月已花费 + 即将发生的成本，给出 pass/warn/block 判定。

当前 cost.calculator.CostTracker 是 per-task 的，没看月度累计。
本模块提供独立、纯函数式判断，方便在 orchestrator / model_selector 调用，
也方便单元测试。

数据来源：从持久化的月度费用文件读取（默认 data/cost/monthly_usage.json）。
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_USAGE_PATH = Path(__file__).resolve().parents[2] / "data" / "cost" / "monthly_usage.json"
WARN_RATIO = 0.8


@dataclass
class BudgetDecision:
    allow: bool
    reason: str
    spent_usd: float
    budget_usd: float
    projected_usd: float
    ratio: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "allow": self.allow,
            "reason": self.reason,
            "spent_usd": round(self.spent_usd, 6),
            "budget_usd": round(self.budget_usd, 6),
            "projected_usd": round(self.projected_usd, 6),
            "ratio": round(self.ratio, 4),
        }


def _current_month_key(now: _dt.datetime | None = None) -> str:
    now = now or _dt.datetime.now()
    return now.strftime("%Y-%m")


def load_monthly_spent(usage_path: Path = DEFAULT_USAGE_PATH, *, now: _dt.datetime | None = None) -> float:
    """读取当前月份的累计花费（USD）。文件不存在或缺字段返回 0。"""
    if not usage_path.exists():
        return 0.0
    try:
        data = json.loads(usage_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0.0
    return float(data.get(_current_month_key(now), 0.0))


def add_monthly_spent(
    delta_usd: float,
    usage_path: Path = DEFAULT_USAGE_PATH,
    *,
    now: _dt.datetime | None = None,
) -> float:
    """累加当月花费并落盘（多进程/多线程安全），返回累计后的金额。"""
    from src.utils.atomic_io import update_json_atomically

    key = _current_month_key(now)

    def _mutate(data: dict) -> dict:
        if not isinstance(data, dict):
            data = {}
        data[key] = float(data.get(key, 0.0)) + float(delta_usd)
        return data

    new_data = update_json_atomically(usage_path, _mutate)
    return float(new_data.get(key, 0.0))


def check_budget(
    *,
    monthly_budget_usd: float,
    upcoming_cost_usd: float = 0.0,
    spent_usd: float | None = None,
    usage_path: Path = DEFAULT_USAGE_PATH,
    warn_ratio: float = WARN_RATIO,
    now: _dt.datetime | None = None,
) -> BudgetDecision:
    """判断"是否允许执行下一次费用为 upcoming_cost_usd 的调用"。

    - allow=False 当 spent + upcoming > budget
    - reason 说明 ok / warn / over_budget / no_budget_set
    - 若 spent_usd 显式传入，则不读文件（便于测试与外部决策）。
    """
    if monthly_budget_usd <= 0:
        return BudgetDecision(
            allow=True, reason="no_budget_set",
            spent_usd=0.0, budget_usd=monthly_budget_usd,
            projected_usd=upcoming_cost_usd, ratio=0.0,
        )

    spent = float(spent_usd) if spent_usd is not None else load_monthly_spent(usage_path, now=now)
    projected = spent + max(0.0, float(upcoming_cost_usd))
    ratio = projected / monthly_budget_usd

    if projected > monthly_budget_usd:
        return BudgetDecision(
            allow=False, reason="over_budget",
            spent_usd=spent, budget_usd=monthly_budget_usd,
            projected_usd=projected, ratio=ratio,
        )
    if ratio >= warn_ratio:
        return BudgetDecision(
            allow=True, reason="warn",
            spent_usd=spent, budget_usd=monthly_budget_usd,
            projected_usd=projected, ratio=ratio,
        )
    return BudgetDecision(
        allow=True, reason="ok",
        spent_usd=spent, budget_usd=monthly_budget_usd,
        projected_usd=projected, ratio=ratio,
    )
