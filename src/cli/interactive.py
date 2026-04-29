"""Dual-mode interactive REPL: Single (smart-routed chat) + Team (plan-confirm-execute).

Single mode (default):
  Every user message → Leader classifies → routes to best available model → reply.
  If no matching model, shows hint + Leader answers itself.

Team mode (/team):
  Phase 1 — Planning: Leader generates a plan; user reviews/modifies in multi-turn dialogue
  Phase 2 — Execution: confirmed plan runs through full Orchestrator pipeline
"""

from __future__ import annotations

import re
import subprocess
import sys
import uuid
from typing import Any

import typer
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt

from src.cli.coro import run_coro
from src.cli.display import console, subtask_tree
from src.config import AppConfig, load_config, load_models_profile, load_user_profile
from src.leader.orchestrator import Orchestrator
from src.leader.query_router import classify, route, ClassifyResult
from src.leader.task_planner import Subtask, TaskPlan, plan_task
from src.memory.rag_engine import query as rag_query
from src.mcp.server import MCPToolRegistry
from src.models.api_model import APIModelWorker
from src.models.local_model import OllamaWorker

# ── Constants ────────────────────────────────────────────────────────────────

_MAX_TURNS = 20  # max user/assistant pairs kept in transcript

SINGLE_SYSTEM = """\
你是 ourAgentTeams 的交互助手，由本机 Ollama 驱动。
- 用清晰、可执行的语言回答；写代码时给出可运行片段。
- 如果收到工具上下文或 RAG 参考，优先依据其中信息回答。"""


# ── Helpers ──────────────────────────────────────────────────────────────────

def _cap_transcript(msgs: list[dict[str, str]]) -> None:
    while len(msgs) > _MAX_TURNS * 2:
        del msgs[0:2]


def _format_context(transcript: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for m in transcript:
        if m["role"] == "user":
            lines.append(f"用户: {m['content']}")
        elif m["role"] == "assistant":
            lines.append(f"助手: {m['content']}")
    if not lines:
        return ""
    return "【对话上下文】\n" + "\n".join(lines[-_MAX_TURNS * 2:])


def _render_reply(text: str) -> None:
    if len(text) < 12_000 and ("```" in text or text.strip().startswith("#")):
        console.print(Markdown(text))
    else:
        console.print(text, highlight=False, markup=False)


def _plan_to_tree_data(plan: TaskPlan) -> list[dict[str, Any]]:
    return [
        {
            "id": s.id,
            "title": s.title,
            "model": s.assigned_model or "pending",
            "status": s.status,
            "importance": s.importance,
        }
        for s in plan.subtasks
    ]


# ── Single mode handler ─────────────────────────────────────────────────────

def _handle_single(
    line: str,
    transcript: list[dict[str, str]],
    cfg: AppConfig,
    leader: OllamaWorker,
    _run,
) -> None:
    """Process one user turn in Single mode: classify → route → answer."""

    cr: ClassifyResult = run_coro(classify(leader, line))

    if cr.complexity == "complex":
        console.print(
            "[dim]此任务看起来较为复杂，你可以用 [bold]/team[/bold] 模式让整个团队协作完成。[/dim]"
        )

    rr = route(cr, cfg)

    if rr.hint:
        console.print(f"[yellow]{rr.hint}[/yellow]")

    messages: list[dict[str, str]] = [{"role": "system", "content": SINGLE_SYSTEM}]

    if cr.needs_tools:
        mcp = MCPToolRegistry()
        rag_results = rag_query(line, n_results=3)
        rag_text = "\n".join(r["text"] for r in rag_results) if rag_results else ""
        extra = ""
        if rag_text:
            extra += f"\n\n【RAG 参考资料】\n{rag_text}"
        extra += f"\n\n{mcp.get_tools_description()}"
        messages[0]["content"] += extra

    for m in transcript:
        messages.append(m)
    messages.append({"role": "user", "content": line})

    chosen_worker = None
    if rr.worker and not rr.is_fallback:
        if rr.worker.model != leader.model:
            if rr.worker.provider == "ollama":
                chosen_worker = OllamaWorker(
                    model=rr.worker.model,
                    base_url=cfg.leader.ollama_base_url,
                )
            else:
                chosen_worker = APIModelWorker(
                    model=rr.worker.model,
                    api_key=rr.worker.api_key,
                )
            if chosen_worker:
                ok = run_coro(chosen_worker.ping())
                if not ok:
                    console.print(
                        f"[yellow]路由模型 {rr.worker.model} 不可用，Leader 接管回答。[/yellow]"
                    )
                    chosen_worker = None

    worker_to_use = chosen_worker or leader
    model_label = worker_to_use.model

    with console.status(f"[bold green]{model_label} 思考中…", spinner="dots"):
        try:
            resp = run_coro(worker_to_use.chat(messages))
        except Exception as exc:
            console.print(f"[red]调用 {model_label} 失败: {exc}[/red]")
            return

    text = (resp.content or "").strip()

    if chosen_worker:
        console.print(f"[dim]( 由 [cyan]{model_label}[/cyan] 回答 )[/dim]")

    _render_reply(text)

    transcript.append({"role": "user", "content": line})
    transcript.append({"role": "assistant", "content": text})
    _cap_transcript(transcript)


# ── Team mode handler ────────────────────────────────────────────────────────

def _handle_team(
    initial_task: str,
    transcript: list[dict[str, str]],
    cfg: AppConfig,
    leader: OllamaWorker,
    _run,
    _progress_callback,
) -> None:
    """Full Team mode: plan → confirm → execute."""

    if not initial_task:
        try:
            initial_task = Prompt.ask(
                "[bold cyan]team[/bold cyan] 请输入任务描述", default=""
            ).strip()
        except (EOFError, KeyboardInterrupt):
            return
        if not initial_task:
            console.print("[yellow]未输入任务，回到 Single 模式。[/yellow]")
            return

    console.print(
        Panel(
            f"[bold]Team 模式[/bold] — 任务: {initial_task}\n"
            "[dim]Leader 正在制定执行计划…[/dim]",
            border_style="magenta",
        )
    )

    profiles = load_models_profile()
    user_profile = load_user_profile()
    user_pref = user_profile.get("natural_language_summary", "")
    rag_results = rag_query(initial_task, n_results=3)
    rag_context = "\n".join(r["text"] for r in rag_results) if rag_results else ""
    mcp = MCPToolRegistry()

    ctx = _format_context(transcript)
    task_with_ctx = (ctx + "\n\n" + initial_task) if ctx else initial_task

    with console.status("[bold green]Leader 规划中…", spinner="dots"):
        plan: TaskPlan = run_coro(plan_task(
            leader,
            task_with_ctx,
            worker_profiles=profiles,
            user_preferences=user_pref,
            rag_context=rag_context,
            tool_context=mcp.get_tools_description(),
        ))

    console.print(f"\n[bold]Analysis:[/bold] {plan.analysis}")
    console.print(subtask_tree(_plan_to_tree_data(plan)))

    # ── Plan confirmation loop ──
    while True:
        console.print(
            "\n[dim]操作: 直接输入修改意见 | [bold]/edit[/bold] 结构化编辑 "
            "| [bold]/go[/bold] 确认执行 | [bold]/cancel[/bold] 取消[/dim]"
        )
        try:
            cmd = Prompt.ask("[bold magenta]team/plan[/bold magenta]", default="").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("[dim]已取消 Team 模式。[/dim]")
            return

        if not cmd:
            continue

        if cmd.lower() in ("/cancel", "cancel"):
            console.print("[dim]已取消，回到 Single 模式。[/dim]")
            return

        if cmd.lower() in ("/go", "go", "y", "yes", "确认", "执行"):
            break

        # ── /edit structured editing ──
        edit_m = re.match(
            r"^/edit\s+(del|add|mod)\s*(.*)", cmd, re.IGNORECASE | re.DOTALL
        )
        if edit_m:
            action = edit_m.group(1).lower()
            arg = edit_m.group(2).strip()
            if action == "del":
                before = len(plan.subtasks)
                plan.subtasks = [s for s in plan.subtasks if s.id != arg]
                if len(plan.subtasks) < before:
                    console.print(f"[green]已删除子任务 {arg}[/green]")
                else:
                    console.print(f"[yellow]未找到子任务 {arg}[/yellow]")
            elif action == "add":
                new_id = f"sub_{uuid.uuid4().hex[:8]}"
                plan.subtasks.append(Subtask(
                    id=new_id,
                    title=arg[:60] or "新子任务",
                    description=arg,
                    importance=6,
                ))
                console.print(f"[green]已添加子任务 {new_id}: {arg[:60]}[/green]")
            elif action == "mod":
                parts = arg.split(maxsplit=1)
                if len(parts) == 2:
                    sid, new_desc = parts
                    found = False
                    for s in plan.subtasks:
                        if s.id == sid:
                            s.description = new_desc
                            s.title = new_desc[:60]
                            found = True
                            break
                    if found:
                        console.print(f"[green]已修改 {sid}[/green]")
                    else:
                        console.print(f"[yellow]未找到 {sid}[/yellow]")
                else:
                    console.print("[yellow]格式: /edit mod sub_1 \"新描述\"[/yellow]")

            console.print(subtask_tree(_plan_to_tree_data(plan)))
            continue

        if cmd.startswith("/edit"):
            console.print(
                "[bold]结构化编辑命令:[/bold]\n"
                "  /edit del <sub_id>         删除子任务\n"
                "  /edit add <描述>           添加子任务\n"
                "  /edit mod <sub_id> <描述>  修改子任务"
            )
            continue

        # ── Natural language modification ──
        console.print("[dim]Leader 根据你的意见重新规划…[/dim]")
        revision_prompt = (
            f"用户对以下执行计划提出了修改意见，请据此调整计划。\n\n"
            f"## 原计划\n{plan.analysis}\n子任务:\n"
            + "\n".join(f"- {s.id}: {s.title} — {s.description}" for s in plan.subtasks)
            + f"\n\n## 用户修改意见\n{cmd}\n\n"
            f"## 原始任务\n{initial_task}"
        )

        with console.status("[bold green]Leader 重新规划中…", spinner="dots"):
            plan = run_coro(plan_task(
                leader,
                revision_prompt,
                worker_profiles=profiles,
                user_preferences=user_pref,
                rag_context=rag_context,
                tool_context=mcp.get_tools_description(),
            ))

        console.print(f"\n[bold]Analysis:[/bold] {plan.analysis}")
        console.print(subtask_tree(_plan_to_tree_data(plan)))

    # ── Phase 2: Execute ──
    console.print(
        Panel("[bold]开始执行[/bold] — Leader 调度 Agent Team 协作", border_style="green")
    )

    orch = Orchestrator(cfg)
    orch.set_progress_callback(_progress_callback)
    orch.plan = plan

    subprocess.Popen(
        [sys.executable, "-m", "src.watchdog", "--session", orch.session_id],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        result = _run(orch.run(task_with_ctx, precomputed_plan=plan))
    except Exception as exc:
        console.print(f"\n[red bold]执行失败:[/red bold] {exc}")
        return

    out = result.final_output.strip()
    console.print("\n[bold]── Leader 整合交付 ──[/bold]\n")
    _render_reply(out)
    console.print(
        f"\n[dim]质量均分: {result.total_quality_avg:.1f}/10 · "
        f"session [cyan]{orch.session_id}[/][/dim]\n"
    )

    transcript.append({"role": "user", "content": f"[Team 任务] {initial_task}"})
    transcript.append({"role": "assistant", "content": out})
    _cap_transcript(transcript)


# ── Main REPL ────────────────────────────────────────────────────────────────

def run_interactive() -> None:
    """Dual-mode REPL entry point."""
    if not sys.stdin.isatty():
        console.print(
            "[yellow]非交互式环境：请使用 [bold]ouragentteams start \"…\"[/bold] "
            "或 [bold]ouragentteams --help[/bold][/yellow]"
        )
        raise typer.Exit(1)

    from src.cli.main import _progress_callback, _run

    cfg = load_config()
    leader = OllamaWorker(model=cfg.leader.model, base_url=cfg.leader.ollama_base_url)

    if not _run(leader.ping()):
        console.print(
            f"[red]无法连接 Ollama（{cfg.leader.ollama_base_url}）或没有模型 "
            f"{cfg.leader.model!r}。[/red]\n"
            f"[dim]请确认 ollama 已运行，并执行: ollama pull {cfg.leader.model}[/dim]"
        )
        raise typer.Exit(1)

    transcript: list[dict[str, str]] = []
    workers_local = ", ".join(w.model for w in cfg.workers_local) or "无"
    workers_api = ", ".join(w.model for w in cfg.workers_api) or "无"

    banner = (
        f"[bold]ourAgentTeams[/bold] — Leader: [cyan]{cfg.leader.model}[/cyan]\n"
        f"[dim]本地模型: {workers_local} · API 模型: {workers_api}[/dim]\n\n"
        "[dim][bold]Single 模式[/bold]（默认）：Leader 智能路由，选择最合适的模型回答每条消息。[/dim]\n"
        "[dim][bold]/team[/bold] <任务>：切换到 Team 模式，Leader 制定计划，整个团队协作执行。[/dim]\n"
        "[dim][bold]/help[/] · [bold]/mode[/] · [bold]/clear[/] · [bold]/exit[/][/dim]"
    )
    console.print(Panel(banner, title="交互模式", border_style="blue"))

    while True:
        prompt_label = "[bold cyan]single[/bold cyan]"
        try:
            line = Prompt.ask(f"\n{prompt_label}", default="").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]再见。[/dim]")
            return

        if not line:
            continue

        # ── Global commands ──
        if line in ("/exit", "/quit", "exit", "quit"):
            console.print("[dim]再见。[/dim]")
            return

        if line in ("/help", "help", "?"):
            console.print(
                "[bold]模式[/bold]\n"
                "  当前默认为 [cyan]Single[/] 模式：Leader 对每条消息做领域分类，"
                "路由到最合适的可用模型回答。\n"
                "  [magenta]/team[/] <任务> 进入 Team 模式：Leader 制定执行计划，"
                "多轮确认后由整个 Agent Team 协作执行。\n\n"
                "[bold]Single 模式命令[/bold]\n"
                "  直接输入 — Leader 分类后路由模型回答\n\n"
                "[bold]Team 模式命令（规划阶段）[/bold]\n"
                "  直接输入 — 自然语言修改计划\n"
                "  [cyan]/edit del[/] <id> — 删除子任务\n"
                "  [cyan]/edit add[/] <描述> — 添加子任务\n"
                "  [cyan]/edit mod[/] <id> <描述> — 修改子任务\n"
                "  [cyan]/go[/] — 确认执行\n"
                "  [cyan]/cancel[/] — 取消回到 Single\n\n"
                "[bold]通用命令[/bold]\n"
                "  [cyan]/team[/] [任务] — 进入 Team 模式\n"
                "  [cyan]/single[/] — 回到 Single 模式\n"
                "  [cyan]/mode[/] — 查看当前模式\n"
                "  [cyan]/clear[/] — 清空对话上下文\n"
                "  [cyan]/exit[/] — 退出"
            )
            continue

        if line in ("/mode",):
            console.print("当前模式: [cyan]Single[/cyan]（输入 /team 进入 Team 流程）")
            continue

        if line in ("/clear", "/reset"):
            transcript.clear()
            console.print("[green]已清空对话上下文。[/green]")
            continue

        if line in ("/single",):
            console.print("[cyan]当前已在 Single 模式。[/cyan]")
            continue

        # ── /team trigger ──
        team_m = re.match(r"^/team\s*(.*)", line, re.DOTALL | re.IGNORECASE)
        if team_m:
            _handle_team(
                team_m.group(1).strip(),
                transcript,
                cfg,
                leader,
                _run,
                _progress_callback,
            )
            continue

        _handle_single(line, transcript, cfg, leader, _run)
