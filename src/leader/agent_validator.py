"""Agent frontmatter validator with clear, user-friendly error messages.

P1-13：当前 agent_registry 在 frontmatter 缺字段时只是 print 一行；
本模块提供独立的、可在 CLI/CI 中调用的校验入口，给出可定位的错误。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)

REQUIRED_FIELDS = ("name", "description")
OPTIONAL_FIELDS = (
    "preferred_models", "fallback_models", "required_skills",
    "temperature", "max_tokens", "plan_mode",
)
PLAN_MODES = {"auto", "always", "never"}


@dataclass
class AgentValidationIssue:
    path: Path
    severity: str  # "error" | "warning"
    field: str | None
    message: str

    def format(self) -> str:
        loc = f"{self.path.name}"
        if self.field:
            loc += f":{self.field}"
        return f"[{self.severity.upper()}] {loc}: {self.message}"


@dataclass
class AgentValidationResult:
    path: Path
    issues: list[AgentValidationIssue] = field(default_factory=list)
    parsed_front: dict[str, Any] | None = None
    body: str = ""

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    def errors(self) -> list[AgentValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    def warnings(self) -> list[AgentValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]


def validate_agent_md(path: Path) -> AgentValidationResult:
    """校验单个 agent MD 文件。返回 issues 列表，不抛异常。"""
    path = Path(path)
    res = AgentValidationResult(path=path)

    if not path.exists():
        res.issues.append(AgentValidationIssue(path, "error", None, "文件不存在"))
        return res

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        res.issues.append(AgentValidationIssue(path, "error", None, f"无法读取：{e}"))
        return res

    m = _FRONTMATTER_RE.match(text)
    if not m:
        res.issues.append(AgentValidationIssue(
            path, "error", None,
            "缺少 YAML frontmatter；请用 ---\\n<yaml>\\n---\\n<system prompt> 格式",
        ))
        return res

    raw_front = m.group(1)
    body = m.group(2).strip()
    res.body = body

    try:
        front = yaml.safe_load(raw_front) or {}
    except yaml.YAMLError as e:
        res.issues.append(AgentValidationIssue(path, "error", None, f"frontmatter YAML 解析失败：{e}"))
        return res

    if not isinstance(front, dict):
        res.issues.append(AgentValidationIssue(path, "error", None, "frontmatter 必须是 mapping (key: value)"))
        return res

    res.parsed_front = front

    # required fields
    for k in REQUIRED_FIELDS:
        if k not in front or front[k] in ("", None):
            res.issues.append(AgentValidationIssue(path, "error", k, f"缺少必填字段 '{k}'"))

    # body
    if not body:
        res.issues.append(AgentValidationIssue(path, "error", "<body>", "system prompt 主体为空"))
    elif len(body) < 20:
        res.issues.append(AgentValidationIssue(path, "warning", "<body>", f"system prompt 过短 ({len(body)} chars)"))

    # type checks
    for list_field in ("preferred_models", "fallback_models", "required_skills"):
        if list_field in front and not isinstance(front[list_field], list):
            res.issues.append(AgentValidationIssue(
                path, "error", list_field,
                f"'{list_field}' 必须是 list，例如 ['gpt-4o', 'claude-sonnet-4-6']",
            ))

    if "temperature" in front:
        try:
            t = float(front["temperature"])
            if not 0.0 <= t <= 2.0:
                res.issues.append(AgentValidationIssue(
                    path, "warning", "temperature", f"temperature={t} 超出常规范围 [0,2]",
                ))
        except (TypeError, ValueError):
            res.issues.append(AgentValidationIssue(
                path, "error", "temperature", f"temperature 必须能转 float，得到 {front['temperature']!r}",
            ))

    if "max_tokens" in front:
        try:
            mt = int(front["max_tokens"])
            if mt <= 0:
                res.issues.append(AgentValidationIssue(
                    path, "error", "max_tokens", f"max_tokens 必须 >0，得到 {mt}",
                ))
        except (TypeError, ValueError):
            res.issues.append(AgentValidationIssue(
                path, "error", "max_tokens", f"max_tokens 必须能转 int，得到 {front['max_tokens']!r}",
            ))

    if "plan_mode" in front:
        pm = front["plan_mode"]
        if pm not in PLAN_MODES:
            res.issues.append(AgentValidationIssue(
                path, "error", "plan_mode", f"plan_mode 必须是 {sorted(PLAN_MODES)} 之一，得到 {pm!r}",
            ))

    # unknown fields → warning
    known = set(REQUIRED_FIELDS) | set(OPTIONAL_FIELDS)
    for k in front:
        if k not in known:
            res.issues.append(AgentValidationIssue(
                path, "warning", k, f"未识别字段 '{k}' (可能是拼写错误)",
            ))

    return res


def validate_agents_dir(agents_dir: Path) -> list[AgentValidationResult]:
    """批量校验目录下所有 *.md。"""
    agents_dir = Path(agents_dir)
    if not agents_dir.exists():
        return []
    return [validate_agent_md(p) for p in sorted(agents_dir.glob("*.md"))]


def format_report(results: list[AgentValidationResult]) -> str:
    lines: list[str] = []
    n_err = n_warn = n_ok = 0
    for r in results:
        if r.ok and not r.warnings():
            n_ok += 1
            continue
        for i in r.issues:
            lines.append(i.format())
            if i.severity == "error":
                n_err += 1
            else:
                n_warn += 1
    summary = f"共检查 {len(results)} 个 agent：{n_ok} OK, {n_warn} warning, {n_err} error"
    return "\n".join(lines + ["", summary]) if lines else summary
