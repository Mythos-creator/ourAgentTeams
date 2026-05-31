"""Health-check command — diagnose configuration / runtime issues.

Run via ``ouragentteams doctor``. Returns:
  0 — everything OK
  1 — at least one warning
  2 — at least one hard failure
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from src.cli.coro import run_coro
from src.cli.display import console, doctor_table


# ─── Public entry ───────────────────────────────────────────────────────────


def run_doctor() -> int:
    rows: list[dict[str, Any]] = []

    rows.append(_check_python_version())
    rows.append(_check_config_exists())
    rows.append(_check_data_dir_writable())
    rows.append(_check_ollama_running())
    rows.append(_check_ollama_models_pulled())
    rows.append(_check_api_keys_configured())
    rows.extend(_check_api_keys_connectivity())
    rows.append(_check_monthly_budget_remaining())

    console.print()
    console.print(doctor_table(rows))
    console.print()

    statuses = [r.get("status", "warn") for r in rows]
    if "fail" in statuses:
        console.print("[red]Some checks failed. Run [cyan]ouragentteams init --force[/cyan] to reconfigure.[/red]")
        return 2
    if "warn" in statuses:
        console.print("[yellow]Some warnings — see details above.[/yellow]")
        return 1
    console.print("[green bold]All checks passed.[/green bold]")
    return 0


# ─── Individual checks ──────────────────────────────────────────────────────


def _check_python_version() -> dict[str, Any]:
    v = sys.version_info
    if v >= (3, 11):
        return {"name": "Python version", "status": "ok",
                "detail": f"{v.major}.{v.minor}.{v.micro}"}
    return {"name": "Python version", "status": "fail",
            "detail": f"{v.major}.{v.minor}.{v.micro} (need ≥ 3.11)"}


def _check_config_exists() -> dict[str, Any]:
    from src.config import CONFIG_DIR, is_initialized
    if is_initialized():
        return {"name": "Configuration",
                "status": "ok",
                "detail": str(CONFIG_DIR / "config.yaml")}
    return {"name": "Configuration",
            "status": "fail",
            "detail": f"No config.yaml at {CONFIG_DIR}. Run `ouragentteams init`."}


def _check_data_dir_writable() -> dict[str, Any]:
    from src.config import DATA_DIR
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        probe = DATA_DIR / ".write_probe"
        probe.write_text("ok")
        probe.unlink()
        return {"name": "Data directory",
                "status": "ok",
                "detail": str(DATA_DIR)}
    except OSError as e:
        return {"name": "Data directory",
                "status": "fail",
                "detail": f"{DATA_DIR} not writable: {e}"}


def _check_ollama_running() -> dict[str, Any]:
    try:
        from src.models.local_model import OllamaWorker
        worker = OllamaWorker(model="__probe__")
        run_coro(worker.list_models())
        return {"name": "Ollama daemon", "status": "ok", "detail": "Reachable"}
    except Exception as e:
        return {"name": "Ollama daemon",
                "status": "warn",
                "detail": f"Not reachable ({e}). Cloud-only mode still works."}


def _check_ollama_models_pulled() -> dict[str, Any]:
    try:
        from src.models.local_model import OllamaWorker
        worker = OllamaWorker(model="__probe__")
        models = run_coro(worker.list_models())
    except Exception:
        return {"name": "Ollama models",
                "status": "warn",
                "detail": "Cannot list (daemon not reachable)"}
    if not models:
        return {"name": "Ollama models",
                "status": "warn",
                "detail": "Daemon up but no models pulled. `ollama pull qwen2.5:7b`"}
    return {"name": "Ollama models",
            "status": "ok",
            "detail": f"{len(models)} pulled: {', '.join(models[:5])}"
                      + (" …" if len(models) > 5 else "")}


_API_KEY_ENVS = {
    "OPENAI_API_KEY": "openai",
    "ANTHROPIC_API_KEY": "anthropic",
    "DEEPSEEK_API_KEY": "deepseek",
    "GOOGLE_API_KEY": "google",
}

_PROVIDER_PROBE = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-haiku-latest",
    "deepseek": "deepseek-chat",
    "google": "gemini-1.5-flash",
}


def _check_api_keys_configured() -> dict[str, Any]:
    present = [env for env in _API_KEY_ENVS if os.environ.get(env)]
    if present:
        return {"name": "API keys present",
                "status": "ok",
                "detail": ", ".join(present)}
    return {"name": "API keys present",
            "status": "warn",
            "detail": "No cloud API keys set. Add via `ouragentteams config add-worker` or edit ~/.config/ouragentteams/.env"}


def _check_api_keys_connectivity() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for env, provider in _API_KEY_ENVS.items():
        key = os.environ.get(env)
        if not key:
            continue
        model = _PROVIDER_PROBE.get(provider)
        if not model:
            continue
        try:
            from src.models.api_model import APIModelWorker
            worker = APIModelWorker(model=model, api_key=key)
            ok = run_coro(worker.ping())
            if ok:
                rows.append({"name": f"{provider} ping",
                             "status": "ok",
                             "detail": f"{model} responding"})
            else:
                rows.append({"name": f"{provider} ping",
                             "status": "fail",
                             "detail": f"{model} ping failed (bad key or rate-limited?)"})
        except Exception as e:
            rows.append({"name": f"{provider} ping",
                         "status": "fail",
                         "detail": f"{model} error: {e}"})
    return rows


def _check_monthly_budget_remaining() -> dict[str, Any]:
    try:
        from src.config import load_config
        cfg = load_config()
    except Exception as e:
        return {"name": "Budget", "status": "warn", "detail": f"Cannot load config: {e}"}

    budget = cfg.cost.monthly_budget_usd
    if budget <= 0:
        return {"name": "Budget", "status": "ok", "detail": "No monthly budget set"}
    try:
        from src.cost.budget_guard import load_monthly_spent
        spent = load_monthly_spent()
        remaining = budget - spent
        if remaining < 0:
            return {"name": "Budget",
                    "status": "warn",
                    "detail": f"Over budget: spent ${spent:.2f} / ${budget:.2f}"}
        if remaining < budget * 0.1:
            return {"name": "Budget",
                    "status": "warn",
                    "detail": f"Low: ${remaining:.2f} of ${budget:.2f} left"}
        return {"name": "Budget",
                "status": "ok",
                "detail": f"${remaining:.2f} of ${budget:.2f} remaining"}
    except Exception:
        return {"name": "Budget",
                "status": "ok",
                "detail": f"${budget:.2f}/month configured"}
