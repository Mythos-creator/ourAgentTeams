"""CLI entry point: all user-facing commands for ourAgentTeams."""

from __future__ import annotations

import datetime
import os
import subprocess
import sys
import time
from getpass import getpass
from typing import Any, Optional

import typer
from rich.prompt import Confirm, Prompt

from src.cli.coro import run_coro as _run
from src.cli.display import (
    console, cost_panel, create_progress, header_panel,
    model_report_table, privacy_result, savings_panel,
    subtask_tree, workers_table,
)
from src.config import (
    AppConfig, DATA_DIR, WorkerEntry, load_config, load_models_profile,
    load_user_profile, save_config, save_user_profile,
)
from src.cost.calculator import CostTracker
from src.leader.orchestrator import Orchestrator
from src.memory.task_history import list_tasks
from src.memory.capability_store import get_all_verdicts, get_savings_suggestions
from src.models.local_model import OllamaWorker

app = typer.Typer(
    name="ourAgentTeams",
    help="ourAgentTeams — local LLM Leader orchestrates your AI workforce.",
)

leader_app = typer.Typer(help="Leader model management")
config_app = typer.Typer(help="Worker model & API key management")
profile_app = typer.Typer(help="User preference management")
report_app = typer.Typer(help="Performance reports")
sessions_app = typer.Typer(help="Session management")

app.add_typer(leader_app, name="leader")
app.add_typer(config_app, name="config")
app.add_typer(profile_app, name="profile")
app.add_typer(report_app, name="report")
app.add_typer(sessions_app, name="sessions")


@app.callback(invoke_without_command=True)
def _default_command(ctx: typer.Context) -> None:
    """With no subcommand, open dual-mode interactive session (Single + Team)."""
    if ctx.invoked_subcommand is None:
        if not sys.stdin.isatty():
            console.print(
                "[yellow]非交互式环境：请使用 [bold]ouragentteams start \"…\"[/bold] "
                "或 [bold]ouragentteams --help[/bold][/yellow]"
            )
            raise typer.Exit(1)
        from src.cli.interactive import run_interactive

        run_interactive()
        raise typer.Exit(0)


async def _progress_callback(event: str, data: dict[str, Any]) -> None:
    """Display events from the Orchestrator."""
    if event == "state":
        state = data.get("state", "")
        state_labels = {
            "privacy_scan": "[Privacy] Scanning for sensitive information...",
            "analysis": "[Analysis] Leader is analyzing the task...",
            "planning": "[Planning] Decomposing into subtasks...",
            "executing": "[Executing] Running subtasks...",
            "reviewing": "[Reviewing] Leader is reviewing results...",
            "integrating": "[Integrating] Merging final output...",
            "delivered": "[Delivered] Task complete!",
            "failed": "[Failed] Task execution failed.",
        }
        label = state_labels.get(state, f"[{state}]")
        if state == "delivered":
            console.print(f"\n[green bold]{label}[/green bold]")
            cost_data = data.get("cost", {})
            if cost_data:
                console.print(cost_panel(cost_data))
        elif state == "failed":
            console.print(f"\n[red bold]{label}[/red bold]")
        else:
            console.print(f"\n{label}")

    elif event == "privacy":
        console.print(privacy_result(data.get("has_sensitive", False), data.get("entity_count", 0)))

    elif event == "plan":
        console.print(f"\n[bold]Analysis:[/bold] {data.get('analysis', '')}")
        console.print(f"Decomposed into [cyan]{data.get('subtask_count', 0)}[/cyan] subtasks")

    elif event == "assignment":
        assignments = data.get("assignments", [])
        tree_data = [
            {"id": a["id"], "title": a.get("title", a["id"]), "model": a.get("model", "?"), "status": "pending"}
            for a in assignments
        ]
        console.print(subtask_tree(tree_data))

    elif event == "subtask_start":
        sid = data.get("subtask_id", "?")
        model = data.get("model", "?")
        retry = data.get("retry", 0)
        retry_text = f" (retry #{retry})" if retry > 0 else ""
        console.print(f"  [yellow]>>>[/yellow] {sid} -> [cyan]{model}[/cyan]{retry_text}")

    elif event == "subtask_done":
        sid = data.get("subtask_id", "?")
        model = data.get("model", "?")
        tokens = data.get("tokens", 0)
        cost = data.get("cost", 0.0)
        elapsed = data.get("elapsed_s", 0.0)
        console.print(f"  [green]OK[/green]  {sid} ({model}) — {tokens} tokens, ${cost:.4f}, {elapsed:.1f}s")

    elif event == "subtask_error":
        sid = data.get("subtask_id", "?")
        error = data.get("error", "unknown")
        retry = data.get("retry", 0)
        console.print(f"  [red]ERR[/red] {sid} — {error} (attempt {retry})")

    elif event == "failover":
        sid = data.get("subtask_id", "?")
        new_model = data.get("new_model", "?")
        console.print(f"  [yellow]FAILOVER[/yellow] {sid} -> [cyan]{new_model}[/cyan]")

    elif event == "review":
        sid = data.get("subtask_id", "?")
        score = data.get("score", 0)
        passed = data.get("passed", True)
        icon = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"
        console.print(f"  [Review] {sid}: score {score:.1f} {icon}")

    elif event == "rework":
        sid = data.get("subtask_id", "?")
        console.print(f"  [yellow]REWORK[/yellow] {sid} — {data.get('reason', '')}")

    elif event == "leader_switch":
        console.print(f"  [bold]Leader switched to [cyan]{data.get('new_model', '?')}[/cyan][/bold]")


# ── Core commands ────────────────────────────────────────

@app.command("chat")
def chat() -> None:
    """Interactive REPL with Single + Team modes (default: Single — smart model routing)."""
    from src.cli.interactive import run_interactive

    run_interactive()


@app.command()
def start(
    task: str = typer.Argument(..., help="Task description"),
    budget: float = typer.Option(None, "--budget", "-b", help="Override budget for this task (USD)"),
    start_watchdog: bool = typer.Option(True, "--watchdog/--no-watchdog", help="Start watchdog process"),
):
    """Submit a task to ourAgentTeams."""
    cfg = load_config()

    console.print(header_panel("initializing...", cfg.leader.model, "received"))

    orch = Orchestrator(cfg)
    if budget is not None:
        orch.cost_tracker.budget_usd = budget

    orch.set_progress_callback(_progress_callback)
    if start_watchdog:
        subprocess.Popen(
            [sys.executable, "-m", "src.watchdog", "--session", orch.session_id],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    try:
        result = _run(orch.run(task))
    except Exception as exc:
        console.print(f"\n[red bold]Error:[/red bold] {exc}")
        raise typer.Exit(1)

    console.print("\n[bold]═══ Final Output ═══[/bold]\n")
    console.print(result.final_output)
    console.print(f"\n[dim]Average quality: {result.total_quality_avg:.1f}/10[/dim]")


@app.command("resume")
def resume(
    session_id: str = typer.Argument("latest", help="Session id or 'latest'"),
):
    """Resume a previous session from snapshot."""
    cfg = load_config()
    sid = session_id
    if sid == "latest":
        sdir = DATA_DIR / "sessions"
        candidates = [p for p in sdir.glob("sess_*") if (p / "state.json").exists()] if sdir.exists() else []
        if not candidates:
            console.print("[red]No resumable session found.[/red]")
            raise typer.Exit(1)
        sid = sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0].name

    try:
        orch = Orchestrator.from_snapshot(sid, cfg=cfg)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    console.print(header_panel(sid, cfg.leader.model, "resuming"))
    orch.set_progress_callback(_progress_callback)
    result = _run(orch.resume())
    console.print("\n[bold]═══ Final Output ═══[/bold]\n")
    console.print(result.final_output)


@sessions_app.command("list")
def sessions_list(
    limit: int = typer.Option(20, "--limit", "-n", help="Max sessions to display"),
):
    """List latest saved sessions."""
    rows = list_tasks(limit=limit)
    if not rows:
        console.print("[dim]No sessions found.[/dim]")
        return
    console.print("[bold]Recent sessions:[/bold]")
    for t in rows:
        completed = t.completed_at.strftime("%Y-%m-%d %H:%M:%S") if t.completed_at else "-"
        console.print(
            f"  - [cyan]{t.id}[/cyan] status={t.status} cost=${t.total_cost_usd:.4f} completed={completed}"
        )


@app.command("schedule")
def schedule(
    task: str = typer.Argument(..., help="Task description"),
    cron: str = typer.Option(..., "--cron", help='Cron expression, e.g. "0 8 * * *"'),
    max_runs: int = typer.Option(0, "--max-runs", help="Stop after N runs; 0 = infinite"),
):
    """Schedule recurring tasks with cron expression."""
    try:
        from croniter import croniter
    except ImportError:
        console.print("[red]croniter not installed. Run pip install croniter[/red]")
        raise typer.Exit(1)

    cfg = load_config()
    itr = croniter(cron, datetime.datetime.now())
    runs = 0
    console.print(f"[bold]Scheduler started[/bold] cron=[cyan]{cron}[/cyan]")
    while True:
        if max_runs and runs >= max_runs:
            console.print("[green]Reached max runs. Exiting scheduler.[/green]")
            return
        nxt = itr.get_next(datetime.datetime)
        wait_s = max((nxt - datetime.datetime.now()).total_seconds(), 0)
        console.print(f"[dim]Next run at {nxt.isoformat(sep=' ', timespec='seconds')}[/dim]")
        time.sleep(wait_s)

        orch = Orchestrator(cfg)
        orch.set_progress_callback(_progress_callback)
        console.print(f"[yellow]Running scheduled task #{runs + 1}[/yellow]")
        _run(orch.run(task))
        runs += 1


@app.command()
def reload():
    """Reload config.yaml for this invocation and show current values."""
    cfg = load_config()
    console.print("[green]Configuration reloaded successfully.[/green]")
    console.print(f"Leader: [cyan]{cfg.leader.model}[/cyan]")
    console.print(f"Workers: {len(cfg.workers_local)} local, {len(cfg.workers_api)} API")
    console.print("[dim]Note: already-running interactive sessions need restart to pick up changes.[/dim]")


# ── Leader commands ──────────────────────────────────────

@leader_app.command("list")
def leader_list():
    """List locally available Ollama models."""
    cfg = load_config()
    worker = OllamaWorker(model="dummy", base_url=cfg.leader.ollama_base_url)
    models = _run(worker.list_models())
    if not models:
        console.print("[yellow]No models found. Is Ollama running?[/yellow]")
        return
    console.print("[bold]Available local models:[/bold]")
    for m in models:
        marker = " [green]<- current leader[/green]" if cfg.leader.model == m else ""
        console.print(f"  - [cyan]{m}[/cyan]{marker}")


@leader_app.command("use")
def leader_use(model: str = typer.Argument(..., help="Model name (e.g. qwen2.5:72b)")):
    """Switch the Leader model (persisted to config.yaml)."""
    cfg = load_config()
    old = cfg.leader.model
    cfg.leader.model = model
    save_config(cfg)
    console.print(f"Leader changed: [red]{old}[/red] -> [green]{model}[/green]")


@leader_app.command("switch")
def leader_switch(
    model: str = typer.Option(..., "--model", "-m", help="New leader model"),
    persist: bool = typer.Option(False, "--persist", help="Write to config.yaml"),
):
    """Hot-switch Leader model (for use during a session)."""
    cfg = load_config()
    old = cfg.leader.model
    cfg.leader.model = model
    if persist:
        save_config(cfg)
    console.print(f"Leader switched: [red]{old}[/red] -> [green]{model}[/green]"
                  + (" [dim](persisted)[/dim]" if persist else " [dim](session only)[/dim]"))


# ── Config commands ──────────────────────────────────────

@config_app.command("list-workers")
def config_list_workers():
    """Show all configured worker models."""
    cfg = load_config()
    console.print(workers_table(cfg.workers_local, cfg.workers_api))


@config_app.command("add-worker")
def config_add_worker(
    model: str = typer.Option(..., "--model", "-m", help="Model name"),
    api_key: Optional[str] = typer.Option(None, "--api-key", "-k", help="API key (omit for local)"),
    strengths: Optional[str] = typer.Option(None, "--strengths", "-s", help="Comma-separated strengths"),
    local: bool = typer.Option(False, "--local", help="Add as local Ollama model"),
):
    """Add a new worker model."""
    cfg = load_config()
    strength_list = [s.strip() for s in strengths.split(",")] if strengths else []

    if local:
        entry = WorkerEntry(model=model, provider="ollama")
        cfg.workers_local.append(entry)
    else:
        if not api_key:
            api_key = getpass("API Key (hidden): ")
        entry = WorkerEntry(model=model, provider="litellm", api_key=api_key, strengths=strength_list)
        cfg.workers_api.append(entry)

    save_config(cfg)
    console.print(f"[green]Added worker:[/green] [cyan]{model}[/cyan] ({'local' if local else 'API'})")

    if not local:
        console.print("Testing connectivity...", end=" ")
        from src.models.api_model import APIModelWorker
        worker = APIModelWorker(model=model, api_key=api_key)
        ok = _run(worker.ping())
        console.print("[green]OK[/green]" if ok else "[red]FAILED[/red]")


@config_app.command("remove-worker")
def config_remove_worker(
    model: str = typer.Option(..., "--model", "-m", help="Model name to remove"),
):
    """Remove a worker model."""
    cfg = load_config()
    orig_local = len(cfg.workers_local)
    orig_api = len(cfg.workers_api)
    cfg.workers_local = [w for w in cfg.workers_local if w.model != model]
    cfg.workers_api = [w for w in cfg.workers_api if w.model != model]

    removed = (len(cfg.workers_local) < orig_local) or (len(cfg.workers_api) < orig_api)
    if removed:
        save_config(cfg)
        console.print(f"[green]Removed:[/green] [cyan]{model}[/cyan]")
    else:
        console.print(f"[yellow]Model not found:[/yellow] {model}")


@config_app.command("verify")
def config_verify():
    """Validate config and test all model connectivity."""
    cfg = load_config()
    console.print("[bold]Verifying configuration...[/bold]\n")

    console.print(f"Leader: [cyan]{cfg.leader.model}[/cyan] ", end="")
    leader = OllamaWorker(model=cfg.leader.model, base_url=cfg.leader.ollama_base_url)
    ok = _run(leader.ping())
    console.print("[green]OK[/green]" if ok else "[red]UNREACHABLE[/red]")

    for w in cfg.workers_local:
        console.print(f"Local worker: [cyan]{w.model}[/cyan] ", end="")
        worker = OllamaWorker(model=w.model, base_url=cfg.leader.ollama_base_url)
        ok = _run(worker.ping())
        console.print("[green]OK[/green]" if ok else "[red]UNREACHABLE[/red]")

    for w in cfg.workers_api:
        console.print(f"API worker: [cyan]{w.model}[/cyan] ", end="")
        from src.models.api_model import APIModelWorker
        worker = APIModelWorker(model=w.model, api_key=w.api_key)
        ok = _run(worker.ping())
        console.print("[green]OK[/green]" if ok else "[red]UNREACHABLE[/red]")


# ── Profile commands ─────────────────────────────────────

@profile_app.command("show")
def profile_show(
    raw: bool = typer.Option(False, "--raw", help="Show raw JSON"),
):
    """Show current user preference profile."""
    import json
    profile = load_user_profile()

    if raw:
        console.print_json(json.dumps(profile, ensure_ascii=False, indent=2))
    else:
        summary = profile.get("natural_language_summary", "")
        if summary:
            console.print(f"\n[bold]User Profile:[/bold]\n{summary}")
        else:
            console.print("[dim]No preferences recorded yet. Use 'ouragentteams profile edit' to set them.[/dim]")

        dims = profile.get("dimensions", {})
        if any(v for v in dims.values() if any(dims[k] for k in dims)):
            console.print("\n[bold]Details:[/bold]")
            for cat, vals in dims.items():
                console.print(f"  [cyan]{cat}[/cyan]: {vals}")


@profile_app.command("edit")
def profile_edit():
    """Interactively edit user preferences via conversation with Leader."""
    cfg = load_config()
    profile = load_user_profile()

    console.print("\n[bold]Profile Editor[/bold]")
    console.print("Tell me about your preferences (type 'done' to finish):\n")

    lines: list[str] = []
    while True:
        line = Prompt.ask("[cyan]You[/cyan]")
        if line.strip().lower() in ("done", "quit", "exit"):
            break
        lines.append(line)

    if lines:
        new_text = " ".join(lines)
        old_summary = profile.get("natural_language_summary", "")
        if old_summary:
            profile["natural_language_summary"] = f"{old_summary} {new_text}"
        else:
            profile["natural_language_summary"] = new_text

        import time
        profile["last_updated"] = time.strftime("%Y-%m-%d")
        profile["update_count"] = profile.get("update_count", 0) + 1
        save_user_profile(profile)
        console.print("[green]Profile updated.[/green]")
    else:
        console.print("[dim]No changes made.[/dim]")


@profile_app.command("reset")
def profile_reset():
    """Reset user profile to defaults."""
    if Confirm.ask("Are you sure you want to reset your profile?"):
        from src.config import DATA_DIR
        p = DATA_DIR / "memory" / "user_profile.json"
        if p.exists():
            p.unlink()
        load_user_profile()
        console.print("[green]Profile reset to defaults.[/green]")


# ── Report commands ──────────────────────────────────────

@report_app.callback(invoke_without_command=True)
def report_default(
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Show single model detail"),
    suggest_savings: bool = typer.Option(False, "--suggest-savings", help="Show cost saving suggestions"),
):
    """View model performance reports."""
    profiles = load_models_profile()

    if not profiles:
        console.print("[dim]No performance data yet. Run some tasks first.[/dim]")
        return

    if model:
        p = profiles.get(model)
        if not p:
            console.print(f"[yellow]No data for model: {model}[/yellow]")
            return
        import json
        console.print(f"\n[bold]{model} — Detailed Report[/bold]")
        console.print_json(json.dumps(p, ensure_ascii=False, indent=2))
        return

    console.print()
    console.print(model_report_table(profiles))

    if suggest_savings:
        suggestions = get_savings_suggestions(profiles)
        console.print()
        console.print(savings_panel(suggestions))


# ── Entry ────────────────────────────────────────────────

def main():
    app()


if __name__ == "__main__":
    main()
