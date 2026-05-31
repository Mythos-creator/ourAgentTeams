"""Rich-based display helpers for the CLI: panels, progress bars, task boards."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

console = Console()


def header_panel(session_id: str, leader_model: str, state: str) -> Panel:
    content = Text()
    content.append(f"  Session: ", style="dim")
    content.append(f"{session_id}\n", style="cyan")
    content.append(f"  Leader:  ", style="dim")
    content.append(f"{leader_model}", style="green bold")
    content.append(f" (local)\n", style="dim")
    content.append(f"  State:   ", style="dim")
    content.append(f"{state}", style="yellow bold")
    return Panel(content, title="[bold]ourAgentTeams[/bold]", border_style="blue")


def subtask_tree(subtasks: list[dict[str, Any]]) -> Tree:
    tree = Tree("[bold]Task Plan[/bold]")
    for i, st in enumerate(subtasks, 1):
        model = st.get("model") or st.get("assigned_model") or "pending"
        title = st.get("title", st.get("id", f"#{i}"))
        importance = st.get("importance", "?")

        status_icon = {
            "pending": "[dim]...[/dim]",
            "running": "[yellow]>>>[/yellow]",
            "completed": "[green]OK[/green]",
            "failed": "[red]FAIL[/red]",
            "timeout": "[red]TIMEOUT[/red]",
        }.get(st.get("status", "pending"), "[dim]...[/dim]")

        tree.add(f"{status_icon} [{i}] {title}  ->  [cyan]{model}[/cyan]  (importance: {importance})")
    return tree


def cost_panel(cost_data: dict[str, Any]) -> Panel:
    spent = cost_data.get("spent_usd", 0.0)
    budget = cost_data.get("budget_usd", 20.0)
    remaining = cost_data.get("remaining_usd", budget - spent)
    pct = (spent / budget * 100) if budget > 0 else 0

    bar_len = 20
    filled = int(pct / 100 * bar_len)
    bar = "[green]" + "█" * filled + "[/green]" + "[dim]" + "░" * (bar_len - filled) + "[/dim]"

    text = f"  {bar}  ${spent:.4f} / ${budget:.2f}  (remaining: ${remaining:.4f})"
    return Panel(text, title="Cost", border_style="dim")


def privacy_result(has_sensitive: bool, entity_count: int) -> str:
    if has_sensitive:
        return f"[yellow]Detected {entity_count} sensitive entities — sanitized for external models[/yellow]"
    return "[green]No sensitive information detected[/green]"


def model_report_table(profiles: dict[str, Any]) -> Table:
    table = Table(title="Model Performance Report", show_lines=True)
    table.add_column("Model", style="cyan", min_width=25)
    table.add_column("Avg Score", justify="center")
    table.add_column("Tasks", justify="center")
    table.add_column("Fail Rate", justify="center")
    table.add_column("Review Pass", justify="center")
    table.add_column("Cost", justify="right")
    table.add_column("Verdict", min_width=20)

    verdict_styles = {
        "recommended": "[green]recommended[/green]",
        "usable": "[blue]usable[/blue]",
        "declining": "[yellow]declining[/yellow]",
        "consider_replacing": "[red]consider_replacing[/red]",
        "not_worth_paying": "[red bold]not_worth_paying[/red bold]",
    }

    for model, p in profiles.items():
        perf = p.get("performance", {})
        q = perf.get("quality", {})
        v = p.get("verdict", {})
        cost_info = perf.get("cost", {})

        avg = q.get("avg_score", 0)
        score_style = "green" if avg >= 8 else ("yellow" if avg >= 6 else "red")

        table.add_row(
            model,
            f"[{score_style}]{avg:.1f}[/{score_style}]",
            str(perf.get("total_tasks", 0)),
            f"{perf.get('failure_rate', 0):.0%}",
            f"{perf.get('review_pass_rate', 0):.0%}",
            f"${cost_info.get('total_cost_usd', 0):.3f}",
            verdict_styles.get(v.get("status", ""), v.get("status", "unknown")),
        )

    return table


def savings_panel(suggestions: list[dict[str, Any]]) -> Panel:
    if not suggestions:
        return Panel("[green]All models are performing well, no savings to suggest.[/green]",
                     title="Savings", border_style="green")

    lines: list[str] = []
    total_save = 0.0
    for s in suggestions:
        lines.append(
            f"  [yellow]{s['action']}[/yellow] [cyan]{s['model']}[/cyan]: {s['reason']}"
            f"  (save ~${s['estimated_monthly_savings_usd']:.2f}/mo)"
        )
        total_save += s["estimated_monthly_savings_usd"]

    lines.append(f"\n  [bold]Total potential savings: ${total_save:.2f}/mo[/bold]")
    return Panel("\n".join(lines), title="Savings Suggestions", border_style="yellow")


def workers_table(local_workers: list, api_workers: list) -> Table:
    table = Table(title="Worker Models", show_lines=True)
    table.add_column("Model", style="cyan", min_width=30)
    table.add_column("Provider", justify="center")
    table.add_column("Type", justify="center")
    table.add_column("Strengths")

    for w in local_workers:
        table.add_row(w.model, w.provider, "[green]local[/green]", ", ".join(w.strengths) or "-")
    for w in api_workers:
        table.add_row(w.model, "API", "[yellow]paid[/yellow]", ", ".join(w.strengths) or "-")

    return table


def create_progress() -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    )


# ── Setup wizard / doctor ────────────────────────────────────────────────────

def wizard_welcome_panel() -> Panel:
    content = Text()
    content.append("👋 Welcome to ", style="bold")
    content.append("ourAgentTeams", style="cyan bold")
    content.append("!\n\n")
    content.append(
        "Looks like this is your first run. Let's set things up.\n"
        "This wizard takes about 1–3 minutes.\n\n",
        style="dim",
    )
    content.append("You can re-run this anytime with: ", style="dim")
    content.append("ouragentteams init --force", style="cyan")
    return Panel(content, title="[bold]Setup[/bold]", border_style="cyan", padding=(1, 2))


def wizard_step_header(step: int, total: int, title: str) -> Panel:
    txt = Text()
    txt.append(f"Step {step}/{total}  ", style="dim")
    txt.append(title, style="bold cyan")
    return Panel(txt, border_style="cyan")


def ollama_status_panel(running: bool, models: list[str]) -> Panel:
    txt = Text()
    if running:
        txt.append("✓ Ollama is running\n", style="green bold")
        if models:
            txt.append(f"  Models pulled: {len(models)}\n", style="dim")
            for m in models[:8]:
                txt.append(f"    • {m}\n", style="cyan")
            if len(models) > 8:
                txt.append(f"    … and {len(models) - 8} more\n", style="dim")
        else:
            txt.append("  No models pulled yet.\n", style="yellow")
    else:
        txt.append("✗ Ollama not detected\n", style="red bold")
        txt.append("  Either it isn't installed, or the daemon is not running.\n", style="dim")
    return Panel(txt, title="[bold]Ollama[/bold]", border_style="green" if running else "yellow")


def doctor_table(rows: list[dict[str, Any]]) -> Table:
    """rows: [{name, status: ok|warn|fail, detail}]"""
    table = Table(title="[bold]Health Check[/bold]", show_lines=False)
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Detail", overflow="fold")
    icon = {"ok": "[green]✓ OK[/green]", "warn": "[yellow]⚠ WARN[/yellow]", "fail": "[red]✗ FAIL[/red]"}
    for r in rows:
        table.add_row(r.get("name", "?"), icon.get(r.get("status", "warn"), "?"), r.get("detail", ""))
    return table
