"""First-run setup wizard.

Creates a fully working configuration at the resolved user-config home:
  - config.yaml         (Leader/workers/cost/privacy)
  - privacy_rules.yaml  (Presidio rules)
  - .env                (API keys, chmod 600)
  - agents/*.md         (built-in agent profiles)

Triggered automatically when no config.yaml exists, or manually via
`ouragentteams init [--force]`.
"""
from __future__ import annotations

import asyncio
import os
import platform
import shutil
import sys
from dataclasses import dataclass
from getpass import getpass
from importlib import resources
from pathlib import Path
from typing import Optional

import yaml
from rich.prompt import Confirm, IntPrompt, Prompt

from src.cli.coro import run_coro
from src.cli.display import (
    console,
    ollama_status_panel,
    wizard_step_header,
    wizard_welcome_panel,
)
from src.config import _resolve_user_home

# ─── Recommended models ─────────────────────────────────────────────────────

RECOMMENDED_LOCAL = [
    {"id": "qwen2.5:7b", "size": "~4.4 GB", "blurb": "Balanced general model (recommended)"},
    {"id": "qwen2.5:3b", "size": "~2.0 GB", "blurb": "Smaller, fits 8GB RAM machines"},
    {"id": "qwen2.5:14b", "size": "~9.0 GB", "blurb": "Stronger reasoning, needs 16GB+"},
    {"id": "llama3.1:8b", "size": "~4.7 GB", "blurb": "Meta's Llama 3.1, well-rounded"},
]

# Models we use to ping each provider (cheap/light models preferred).
PROVIDER_PROBE = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-haiku-latest",
    "deepseek": "deepseek-chat",
    "google": "gemini-1.5-flash",
}

PROVIDER_INFO = [
    ("deepseek", "DeepSeek", "DEEPSEEK_API_KEY", "Cheap & strong (recommended for cost-sensitive use)"),
    ("anthropic", "Anthropic", "ANTHROPIC_API_KEY", "Claude family — top quality on reasoning/writing"),
    ("openai", "OpenAI", "OPENAI_API_KEY", "GPT family — broad ecosystem"),
    ("google", "Google", "GOOGLE_API_KEY", "Gemini family — multi-modal"),
]


@dataclass
class WizardChoices:
    mode: str  # "local" | "cloud" | "hybrid"
    leader_model: Optional[str] = None
    pulled_models: list[str] = None  # type: ignore[assignment]
    api_keys: dict[str, str] = None  # type: ignore[assignment]
    monthly_budget_usd: float = 20.0


# ─── Public entry ───────────────────────────────────────────────────────────


def run_first_run_wizard(*, force: bool = False) -> None:
    """Run the interactive wizard. After return the user-config dir is fully populated."""
    home = _resolve_user_home()
    cfg_path = home / "config.yaml"

    if cfg_path.exists() and not force:
        console.print(f"[yellow]Configuration already exists at {cfg_path}.[/yellow]")
        console.print("Use [cyan]ouragentteams init --force[/cyan] to overwrite, "
                      "or [cyan]ouragentteams doctor[/cyan] to inspect.")
        return

    console.print()
    console.print(wizard_welcome_panel())
    console.print()

    choices = WizardChoices(mode="hybrid", pulled_models=[], api_keys={})

    # Step 1 — pick mode
    console.print(wizard_step_header(1, 4, "Choose mode"))
    choices.mode = _ask_mode()

    # Step 2 — Ollama (if local/hybrid)
    if choices.mode in ("local", "hybrid"):
        console.print(wizard_step_header(2, 4, "Local Ollama"))
        _ensure_ollama_step(choices)
    else:
        console.print(wizard_step_header(2, 4, "Local Ollama (skipped — cloud-only mode)"))

    # Step 3 — API keys (if cloud/hybrid)
    if choices.mode in ("cloud", "hybrid"):
        console.print(wizard_step_header(3, 4, "Cloud API keys"))
        _configure_api_keys_step(choices)
    else:
        console.print(wizard_step_header(3, 4, "Cloud API keys (skipped — local-only mode)"))

    # Step 4 — write config
    console.print(wizard_step_header(4, 4, "Write configuration"))
    _write_config_files(choices, home)

    _finalize(home)


# ─── Step 1: mode ───────────────────────────────────────────────────────────


def _ask_mode() -> str:
    console.print()
    console.print("  [bold cyan]1.[/bold cyan] Local only   — Ollama on your machine; private, free")
    console.print("  [bold cyan]2.[/bold cyan] Cloud only   — OpenAI / Anthropic / DeepSeek …; needs API keys")
    console.print("  [bold cyan]3.[/bold cyan] Hybrid       — Local Leader + cloud Workers ⭐ recommended")
    console.print()
    choice = IntPrompt.ask("Pick mode", choices=["1", "2", "3"], default=3)
    return {1: "local", 2: "cloud", 3: "hybrid"}[choice]


# ─── Step 2: Ollama ─────────────────────────────────────────────────────────


def _ensure_ollama_step(choices: WizardChoices) -> None:
    running, models = _detect_ollama()
    console.print(ollama_status_panel(running, models))

    if not running:
        _show_ollama_install_hint()
        if not Confirm.ask(
            "\nInstalled Ollama and started the daemon? (Re-detect now)",
            default=False,
        ):
            console.print("[yellow]Skipping Ollama — you can configure it later.[/yellow]")
            return
        running, models = _detect_ollama()
        if not running:
            console.print("[red]Still cannot reach Ollama. Skipping local setup.[/red]")
            return
        console.print("[green]✓ Ollama is up.[/green]\n")

    choices.pulled_models = list(models)
    _pull_recommended_model(choices)


def _detect_ollama() -> tuple[bool, list[str]]:
    """Return (running, models_pulled)."""
    try:
        from src.models.local_model import OllamaWorker

        worker = OllamaWorker(model="__probe__")
        models = run_coro(worker.list_models())
        # If the listing succeeded (even if empty), daemon is up.
        return True, models
    except Exception:
        return False, []


def _show_ollama_install_hint() -> None:
    sysname = platform.system().lower()
    console.print("\n[bold]Install Ollama[/bold]")
    if sysname == "darwin":
        console.print("  • Homebrew:  [cyan]brew install ollama[/cyan]")
        console.print("  • Or download: [cyan]https://ollama.com/download[/cyan]")
        console.print("  • After install, run: [cyan]ollama serve[/cyan] (or it auto-starts)")
    elif sysname == "linux":
        console.print("  • One-liner: [cyan]curl -fsSL https://ollama.com/install.sh | sh[/cyan]")
        console.print("  • Then start: [cyan]ollama serve[/cyan]")
    elif sysname == "windows":
        console.print("  • Download:  [cyan]https://ollama.com/download[/cyan]")
    else:
        console.print(f"  • Visit: [cyan]https://ollama.com/download[/cyan] (platform: {sysname})")


def _pull_recommended_model(choices: WizardChoices) -> None:
    console.print("\n[bold]Pull a model[/bold] (skip if you already have what you need)")
    for i, m in enumerate(RECOMMENDED_LOCAL, 1):
        marker = "  [dim](already pulled)[/dim]" if any(m["id"] in p for p in choices.pulled_models) else ""
        console.print(f"  [cyan]{i}[/cyan]. {m['id']}  [dim]({m['size']})[/dim] — {m['blurb']}{marker}")
    console.print("  [cyan]0[/cyan]. Skip pulling")

    pick = IntPrompt.ask(
        "Choose a model to pull",
        choices=[str(i) for i in range(0, len(RECOMMENDED_LOCAL) + 1)],
        default=1,
    )
    if pick == 0:
        return
    target = RECOMMENDED_LOCAL[pick - 1]["id"]
    if any(target in p for p in choices.pulled_models):
        console.print(f"[green]Already have {target}, skipping pull.[/green]")
        choices.leader_model = target
        return

    console.print(f"\n[dim]Pulling {target}… this can take a while.[/dim]")
    ok = _do_pull(target)
    if ok:
        choices.pulled_models.append(target)
        choices.leader_model = target
        console.print(f"[green]✓ Pulled {target}[/green]")
    else:
        console.print(f"[red]✗ Failed to pull {target}.[/red] You can try later: [cyan]ollama pull {target}[/cyan]")


def _do_pull(model: str) -> bool:
    """Pull a model with progress display. Returns True on success."""
    try:
        import ollama  # type: ignore
        from rich.progress import (
            BarColumn, DownloadColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn,
        )
        client = ollama.Client()
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(f"pull {model}", total=None)
            for chunk in client.pull(model, stream=True):
                # chunk has fields like status, completed, total
                if isinstance(chunk, dict):
                    total = chunk.get("total")
                    completed = chunk.get("completed")
                    status = chunk.get("status", "")
                else:
                    total = getattr(chunk, "total", None)
                    completed = getattr(chunk, "completed", None)
                    status = getattr(chunk, "status", "")
                if total:
                    progress.update(task, total=total, completed=completed or 0,
                                    description=f"{model} — {status}")
                else:
                    progress.update(task, description=f"{model} — {status}")
        return True
    except Exception as exc:
        console.print(f"[red]pull error: {exc}[/red]")
        return False


# ─── Step 3: API keys ───────────────────────────────────────────────────────


def _configure_api_keys_step(choices: WizardChoices) -> None:
    console.print()
    console.print("Pick which providers to configure (toggle by entering numbers separated by commas):")
    for i, (_id, name, _env, blurb) in enumerate(PROVIDER_INFO, 1):
        console.print(f"  [cyan]{i}[/cyan]. {name}  [dim]— {blurb}[/dim]")
    console.print("  [cyan]0[/cyan]. None / skip")
    console.print()

    raw = Prompt.ask("Selection", default="1,2")
    indices = _parse_csv_int(raw, max_val=len(PROVIDER_INFO))
    if not indices:
        console.print("[yellow]No providers selected.[/yellow]")
        return

    for idx in indices:
        provider_id, name, env_name, _blurb = PROVIDER_INFO[idx - 1]
        console.print(f"\n[bold]{name}[/bold]")
        for attempt in range(2):
            key = getpass(f"  {env_name} (hidden): ").strip()
            if not key:
                console.print("[yellow]  Empty — skipping.[/yellow]")
                break
            console.print("  [dim]testing connectivity…[/dim]", end=" ")
            ok = _ping_provider(provider_id, key)
            if ok:
                console.print("[green]OK[/green]")
                choices.api_keys[env_name] = key
                break
            else:
                console.print("[red]FAILED[/red]")
                if attempt == 0 and Confirm.ask("  Retry?", default=True):
                    continue
                if Confirm.ask("  Save key anyway (you can fix later)?", default=False):
                    choices.api_keys[env_name] = key
                break


def _ping_provider(provider_id: str, api_key: str) -> bool:
    model = PROVIDER_PROBE.get(provider_id)
    if not model:
        return False
    try:
        from src.models.api_model import APIModelWorker
        worker = APIModelWorker(model=model, api_key=api_key)
        return run_coro(worker.ping())
    except Exception:
        return False


def _parse_csv_int(s: str, *, max_val: int) -> list[int]:
    out: list[int] = []
    for token in s.replace(" ", "").split(","):
        if not token:
            continue
        try:
            v = int(token)
        except ValueError:
            continue
        if 1 <= v <= max_val and v not in out:
            out.append(v)
    return out


# ─── Step 4: write files ────────────────────────────────────────────────────


def _write_config_files(choices: WizardChoices, home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "agents").mkdir(parents=True, exist_ok=True)

    # config.yaml — start from template, mutate based on choices
    cfg = _load_template_yaml("config.yaml.tpl")

    # Leader
    if choices.mode in ("local", "hybrid") and choices.leader_model:
        cfg["leader"]["model"] = choices.leader_model
    elif choices.mode == "cloud":
        # Cloud-only: pick a cloud model as Leader from configured keys
        cfg["leader"]["provider"] = "litellm"
        cfg["leader"]["model"] = _default_cloud_leader(choices.api_keys) or "gpt-4o-mini"

    # Workers
    cfg["workers"] = {"local": [], "api": []}
    if choices.mode in ("local", "hybrid"):
        for m in choices.pulled_models or ([choices.leader_model] if choices.leader_model else []):
            if m:
                cfg["workers"]["local"].append({
                    "model": m, "provider": "ollama",
                    "strengths": ["general", "coding"],
                })
    if choices.mode in ("cloud", "hybrid"):
        for env_name in choices.api_keys:
            mdl = _model_for_env(env_name)
            if mdl:
                cfg["workers"]["api"].append({
                    "model": mdl, "provider": "litellm",
                    "strengths": ["general"],
                })

    cfg["cost"]["monthly_budget_usd"] = choices.monthly_budget_usd

    (home / "config.yaml").write_text(
        yaml.dump(cfg, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )

    # privacy_rules.yaml — straight copy
    _copy_template("privacy_rules.yaml.tpl", home / "privacy_rules.yaml")

    # .env — straight template + selected keys, chmod 600
    env_path = home / ".env"
    env_text = _read_template("env.tpl")
    if choices.api_keys:
        env_text += "\n# ── Configured by setup wizard ─────────────────────────────\n"
        for k, v in choices.api_keys.items():
            env_text += f"{k}={v}\n"
    env_path.write_text(env_text, encoding="utf-8")
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass

    # agents/*.md — copy bundled agent profiles
    _copy_bundled_agents(home / "agents")

    # data dir — touched lazily by app, but make sure parent exists
    from src.config import _resolve_data_dir
    _resolve_data_dir().mkdir(parents=True, exist_ok=True)


def _default_cloud_leader(api_keys: dict[str, str]) -> Optional[str]:
    if "ANTHROPIC_API_KEY" in api_keys:
        return "claude-3-5-sonnet-latest"
    if "OPENAI_API_KEY" in api_keys:
        return "gpt-4o-mini"
    if "DEEPSEEK_API_KEY" in api_keys:
        return "deepseek-chat"
    if "GOOGLE_API_KEY" in api_keys:
        return "gemini-1.5-pro"
    return None


def _model_for_env(env_name: str) -> Optional[str]:
    return {
        "OPENAI_API_KEY": "gpt-4o-mini",
        "ANTHROPIC_API_KEY": "claude-3-5-sonnet-latest",
        "DEEPSEEK_API_KEY": "deepseek-chat",
        "GOOGLE_API_KEY": "gemini-1.5-pro",
    }.get(env_name)


def _read_template(name: str) -> str:
    files = resources.files("src._templates")
    return (files / name).read_text(encoding="utf-8")


def _load_template_yaml(name: str) -> dict:
    return yaml.safe_load(_read_template(name)) or {}


def _copy_template(name: str, dest: Path) -> None:
    dest.write_text(_read_template(name), encoding="utf-8")


def _copy_bundled_agents(dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    files = resources.files("src._templates.agents")
    for entry in files.iterdir():
        n = entry.name
        if n.endswith(".md"):
            (dest_dir / n).write_text(entry.read_text(encoding="utf-8"), encoding="utf-8")


# ─── Finalize ───────────────────────────────────────────────────────────────


def _finalize(home: Path) -> None:
    console.print()
    console.print(f"[green bold]✓ Setup complete![/green bold]")
    console.print(f"  Configuration: [cyan]{home}[/cyan]")
    console.print()
    console.print("[bold]Next:[/bold]")
    console.print("  • Run [cyan]ouragentteams[/cyan] to start an interactive session")
    console.print("  • Or  [cyan]ouragentteams start \"your task\"[/cyan]")
    console.print("  • Run [cyan]ouragentteams doctor[/cyan] to verify everything")
    console.print()
