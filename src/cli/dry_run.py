"""dry-run 子命令：只规划不执行，便于调试 Leader/路由决策。

把"读 query → 路由 → 拆任务 → 选模型"链路串成只读流程；
不调用任何模型 API（路由/选择本身已在内部决定是否调远端）。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from src.config import AppConfig, load_config
from src.leader.agent_registry import AgentRegistry
from src.leader.dependency_validator import (
    DependencyCycleError, DependencyValidationError,
    topological_sort, validate_dependencies,
)


@dataclass
class DryRunReport:
    query: str
    mode: str  # single | team | unknown
    detected_skills: list[str] = field(default_factory=list)
    selected_agents: list[dict[str, Any]] = field(default_factory=list)
    subtasks: list[dict[str, Any]] = field(default_factory=list)
    sorted_order: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    def to_text(self) -> str:
        lines = [
            f"Query: {self.query}",
            f"Mode:  {self.mode}",
        ]
        if self.detected_skills:
            lines.append(f"Skills: {', '.join(self.detected_skills)}")
        if self.selected_agents:
            lines.append("Selected agents:")
            for a in self.selected_agents:
                lines.append(f"  - {a.get('name')} (model={a.get('model','?')})")
        if self.subtasks:
            lines.append(f"Subtasks ({len(self.subtasks)}):")
            for st in self.subtasks:
                deps = st.get("depends_on", [])
                lines.append(f"  - [{st.get('id')}] {st.get('description','')}"
                             + (f" deps={deps}" if deps else ""))
        if self.sorted_order:
            lines.append(f"Topo order: {' -> '.join(self.sorted_order)}")
        if self.issues:
            lines.append("Issues:")
            for i in self.issues:
                lines.append(f"  ! {i}")
        return "\n".join(lines)


def _detect_skills(query: str, registry: AgentRegistry) -> list[str]:
    """启发式：把所有 agent 的 required_skills 聚合，取在 query 中出现的关键词。"""
    q = query.lower()
    found: list[str] = []
    seen: set[str] = set()
    for a in registry.all():
        for s in a.required_skills:
            sl = s.lower()
            if sl in q and sl not in seen:
                seen.add(sl)
                found.append(s)
    return found


def _classify_mode(query: str, subtasks: list[dict[str, Any]] | None) -> str:
    if subtasks and len(subtasks) > 1:
        return "team"
    # heuristic: long, multi-clause queries → team
    if len(query) > 240 or query.count("。") + query.count(".") >= 3:
        return "team"
    return "single"


def dry_run(
    query: str,
    *,
    cfg: AppConfig | None = None,
    subtasks: list[dict[str, Any]] | None = None,
) -> DryRunReport:
    """Run all read-only planning steps for a query.

    `subtasks` 可由调用方提供（例如已从 task_planner 拿到），
    或留空仅做基础路由分析。
    """
    if cfg is None:
        try:
            cfg = load_config()
        except Exception as e:  # tolerate missing config in dev
            cfg = None  # type: ignore[assignment]
            issues_init = [f"load_config failed: {e}"]
        else:
            issues_init = []
    else:
        issues_init = []

    rep = DryRunReport(query=query, mode="unknown", issues=list(issues_init))

    try:
        registry = AgentRegistry()
    except Exception as e:
        rep.issues.append(f"AgentRegistry load failed: {e}")
        return rep

    skills = _detect_skills(query, registry)
    rep.detected_skills = skills

    best = registry.best_for(skills) if skills else None
    if best:
        model = None
        if cfg is not None:
            try:
                model = best.resolve_model(cfg)
            except Exception:
                model = None
        rep.selected_agents.append({
            "name": best.name,
            "model": model,
            "skills": best.required_skills,
        })

    rep.mode = _classify_mode(query, subtasks)

    if subtasks:
        rep.subtasks = subtasks
        try:
            validate_dependencies(subtasks)
            ordered = topological_sort(subtasks)
            rep.sorted_order = [s.get("id", "?") for s in ordered]
        except (DependencyValidationError, DependencyCycleError) as e:
            rep.issues.append(str(e))

    return rep
