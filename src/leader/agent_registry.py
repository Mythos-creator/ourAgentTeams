"""Agent 角色注册表：扫描 agents/*.md，解析 frontmatter + body 为 AgentProfile。

独立模块，不依赖 orchestrator。当前主要被 eval/ 使用。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.config import AppConfig, _resolve_user_home
from src.memory.capability_store import get_profile


def _resolve_agents_dir() -> Path:
    """Resolve agents/ directory: env var → cwd/agents → ~/.config/ouragentteams/agents → project."""
    import os
    if env := os.environ.get("OURAGENTTEAMS_HOME"):
        return Path(env).expanduser().resolve() / "agents"
    cwd_agents = Path.cwd() / "agents"
    if cwd_agents.exists():
        return cwd_agents
    project_agents = Path(__file__).resolve().parents[2] / "agents"
    if project_agents.exists():
        return project_agents
    return _resolve_user_home() / "agents"


AGENTS_DIR = _resolve_agents_dir()


@dataclass
class AgentProfile:
    name: str
    description: str
    system_prompt: str
    preferred_models: list[str] = field(default_factory=list)
    fallback_models: list[str] = field(default_factory=list)
    required_skills: list[str] = field(default_factory=list)
    temperature: float = 0.3
    max_tokens: int = 4096
    plan_mode: str = "auto"

    def match_score(self, required_skills: list[str]) -> float:
        req = {s.lower() for s in required_skills}
        own = {s.lower() for s in self.required_skills}
        overlap = len(req & own)
        if self.preferred_models:
            qs = []
            for m in self.preferred_models:
                p = get_profile(m)
                qs.append(p.get("performance", {}).get("quality", {}).get("avg_score", 5.0))
            avg_q = sum(qs) / len(qs)
        else:
            avg_q = 5.0
        return overlap * 3.0 + avg_q

    def resolve_model(self, cfg: AppConfig, over_budget: bool = False) -> str | None:
        all_workers = {w.model for w in cfg.workers_api + cfg.workers_local}
        local_only = {w.model for w in cfg.workers_local}
        candidates = (
            self.preferred_models
            if not over_budget
            else [m for m in self.preferred_models if m in local_only]
        )
        for m in candidates:
            if m in all_workers:
                return m
        for m in self.fallback_models:
            if m in all_workers:
                return m
        return next(iter(local_only), None)


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


def parse_agent_md(path: Path) -> AgentProfile:
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError(f"agent file missing frontmatter: {path}")
    front = yaml.safe_load(m.group(1)) or {}
    body = m.group(2).strip()
    return AgentProfile(
        name=front.get("name", path.stem),
        description=front.get("description", ""),
        system_prompt=body,
        preferred_models=front.get("preferred_models", []),
        fallback_models=front.get("fallback_models", []),
        required_skills=front.get("required_skills", []),
        temperature=float(front.get("temperature", 0.3)),
        max_tokens=int(front.get("max_tokens", 4096)),
        plan_mode=front.get("plan_mode", "auto"),
    )


class AgentRegistry:
    def __init__(self, agents_dir: Path = AGENTS_DIR):
        self.agents_dir = agents_dir
        self._cache: dict[str, AgentProfile] = {}
        self.reload()

    def reload(self) -> None:
        self._cache.clear()
        if not self.agents_dir.exists():
            return
        for p in self.agents_dir.glob("*.md"):
            try:
                ap = parse_agent_md(p)
                self._cache[ap.name] = ap
            except Exception as e:
                print(f"[agent_registry] skip {p}: {e}")

    def all(self) -> list[AgentProfile]:
        return list(self._cache.values())

    def get(self, name: str) -> AgentProfile | None:
        return self._cache.get(name)

    def best_for(self, required_skills: list[str]) -> AgentProfile | None:
        if not self._cache:
            return None
        scored = [(a, a.match_score(required_skills)) for a in self._cache.values()]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[0][0]

    def describe_for_planner(self) -> str:
        lines = []
        for a in self._cache.values():
            lines.append(f"- {a.name}: {a.description} | 擅长: {', '.join(a.required_skills)}")
        return "\n".join(lines) or "无可用 agent 角色"
