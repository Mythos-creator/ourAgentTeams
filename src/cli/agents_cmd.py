"""`agents` 子命令实现：列出 / 校验 agents/*.md。

独立于 main.py（main.py 当前被锁定）。可被 Typer/argparse/手工调用。
返回值约定：0 全部 OK，1 有 error，2 有 warning 但无 error。
"""

from __future__ import annotations

import json
from pathlib import Path

from src.leader.agent_registry import AGENTS_DIR, AgentRegistry
from src.leader.agent_validator import format_report, validate_agents_dir


def cmd_list(agents_dir: Path = AGENTS_DIR, *, as_json: bool = False) -> str:
    reg = AgentRegistry(agents_dir=agents_dir)
    profiles = reg.all()
    if as_json:
        return json.dumps([
            {
                "name": p.name,
                "description": p.description,
                "preferred_models": p.preferred_models,
                "fallback_models": p.fallback_models,
                "required_skills": p.required_skills,
                "temperature": p.temperature,
                "max_tokens": p.max_tokens,
                "plan_mode": p.plan_mode,
            } for p in profiles
        ], ensure_ascii=False, indent=2)
    if not profiles:
        return "(no agents found)"
    lines = []
    for p in profiles:
        lines.append(f"- {p.name}")
        lines.append(f"    描述: {p.description}")
        if p.required_skills:
            lines.append(f"    技能: {', '.join(p.required_skills)}")
        if p.preferred_models:
            lines.append(f"    优先模型: {', '.join(p.preferred_models)}")
    return "\n".join(lines)


def cmd_validate(agents_dir: Path = AGENTS_DIR) -> tuple[int, str]:
    """返回 (exit_code, report_text)。"""
    results = validate_agents_dir(agents_dir)
    text = format_report(results)
    has_error = any(not r.ok for r in results)
    has_warn = any(r.warnings() for r in results)
    if has_error:
        return 1, text
    if has_warn:
        return 2, text
    return 0, text
