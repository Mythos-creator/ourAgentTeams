"""Unified configuration loader with env-var interpolation and hot-reload."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = _ROOT / "config"
DATA_DIR = _ROOT / "data"

ENV_VAR_RE = re.compile(r"\$\{(\w+)\}")


def _interpolate_env(value: str) -> str:
    """Replace ${VAR} references with actual environment variable values."""
    def _replacer(m: re.Match) -> str:
        return os.environ.get(m.group(1), m.group(0))
    return ENV_VAR_RE.sub(_replacer, value)


def _walk_interpolate(obj: Any) -> Any:
    if isinstance(obj, str):
        return _interpolate_env(obj)
    if isinstance(obj, dict):
        return {k: _walk_interpolate(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk_interpolate(v) for v in obj]
    return obj


@dataclass
class LeaderConfig:
    model: str = "qwen2.5:72b"
    provider: str = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    fallbacks: list[dict[str, str]] = field(default_factory=list)
    heartbeat_interval_s: int = 5
    watchdog_timeout_s: int = 30


@dataclass
class MonitorConfig:
    heartbeat_interval_s: int = 10
    timeout_threshold_s: int = 120
    max_retries: int = 3


@dataclass
class CostConfig:
    monthly_budget_usd: float = 20.0
    importance_threshold_local: int = 5
    importance_threshold_best: int = 8


@dataclass
class PrivacyConfig:
    enabled: bool = True
    entities: list[str] = field(default_factory=lambda: [
        "PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER",
        "CREDIT_CARD", "API_KEY", "PASSWORD",
    ])


@dataclass
class WorkerEntry:
    model: str
    provider: str = "ollama"
    api_key: str | None = None
    strengths: list[str] = field(default_factory=list)


@dataclass
class AppConfig:
    leader: LeaderConfig = field(default_factory=LeaderConfig)
    workers_local: list[WorkerEntry] = field(default_factory=list)
    workers_api: list[WorkerEntry] = field(default_factory=list)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    cost: CostConfig = field(default_factory=CostConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)

    _config_path: Path = field(default=CONFIG_DIR / "config.yaml", repr=False)
    _mtime: float = field(default=0.0, repr=False)

    @property
    def all_workers(self) -> list[WorkerEntry]:
        return self.workers_local + self.workers_api


def _parse_workers(raw: dict) -> tuple[list[WorkerEntry], list[WorkerEntry]]:
    local = [
        WorkerEntry(model=w["model"], provider=w.get("provider", "ollama"))
        for w in (raw.get("local") or [])
    ]
    api = [
        WorkerEntry(
            model=w["model"],
            provider=w.get("provider", "litellm"),
            api_key=w.get("api_key"),
            strengths=w.get("strengths", []),
        )
        for w in (raw.get("api") or [])
    ]
    return local, api


def load_config(path: Path | None = None) -> AppConfig:
    path = path or (CONFIG_DIR / "config.yaml")
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    raw = _walk_interpolate(raw)

    leader_raw = raw.get("leader", {})
    leader = LeaderConfig(
        model=leader_raw.get("model", "qwen2.5:72b"),
        provider=leader_raw.get("provider", "ollama"),
        ollama_base_url=leader_raw.get("ollama_base_url", "http://localhost:11434"),
        fallbacks=leader_raw.get("fallbacks", []),
        heartbeat_interval_s=leader_raw.get("heartbeat_interval_s", 5),
        watchdog_timeout_s=leader_raw.get("watchdog_timeout_s", 30),
    )

    workers_local, workers_api = _parse_workers(raw.get("workers", {}))

    monitor_raw = raw.get("monitor", {})
    monitor = MonitorConfig(**{k: monitor_raw[k] for k in monitor_raw if k in MonitorConfig.__dataclass_fields__})

    cost_raw = raw.get("cost", {})
    cost = CostConfig(**{k: cost_raw[k] for k in cost_raw if k in CostConfig.__dataclass_fields__})

    priv_raw = raw.get("privacy", {})
    _default_entities = ["PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "API_KEY", "PASSWORD"]
    privacy = PrivacyConfig(
        enabled=priv_raw.get("enabled", True),
        entities=priv_raw.get("entities", _default_entities),
    )

    cfg = AppConfig(
        leader=leader,
        workers_local=workers_local,
        workers_api=workers_api,
        monitor=monitor,
        cost=cost,
        privacy=privacy,
        _config_path=path,
        _mtime=path.stat().st_mtime,
    )
    return cfg


def save_config(cfg: AppConfig, path: Path | None = None) -> None:
    """Serialize the live AppConfig back to config.yaml."""
    path = path or cfg._config_path

    data: dict[str, Any] = {
        "leader": {
            "model": cfg.leader.model,
            "provider": cfg.leader.provider,
            "ollama_base_url": cfg.leader.ollama_base_url,
            "fallbacks": cfg.leader.fallbacks,
            "heartbeat_interval_s": cfg.leader.heartbeat_interval_s,
            "watchdog_timeout_s": cfg.leader.watchdog_timeout_s,
        },
        "workers": {
            "local": [{"model": w.model, "provider": w.provider} for w in cfg.workers_local],
            "api": [
                {
                    k: v
                    for k, v in {
                        "model": w.model,
                        "api_key": w.api_key,
                        "strengths": w.strengths or None,
                    }.items()
                    if v is not None
                }
                for w in cfg.workers_api
            ],
        },
        "monitor": {
            "heartbeat_interval_s": cfg.monitor.heartbeat_interval_s,
            "timeout_threshold_s": cfg.monitor.timeout_threshold_s,
            "max_retries": cfg.monitor.max_retries,
        },
        "cost": {
            "monthly_budget_usd": cfg.cost.monthly_budget_usd,
            "importance_threshold_local": cfg.cost.importance_threshold_local,
            "importance_threshold_best": cfg.cost.importance_threshold_best,
        },
        "privacy": {
            "enabled": cfg.privacy.enabled,
            "entities": cfg.privacy.entities,
        },
    }

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    cfg._mtime = path.stat().st_mtime


def config_changed(cfg: AppConfig) -> bool:
    """Return True if the config file has been modified since last load."""
    try:
        return cfg._config_path.stat().st_mtime > cfg._mtime
    except FileNotFoundError:
        return False


def reload_config(cfg: AppConfig) -> AppConfig:
    """Reload config from disk if it changed."""
    if config_changed(cfg):
        return load_config(cfg._config_path)
    return cfg


def load_models_profile() -> dict:
    path = CONFIG_DIR / "models_profile.json"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_models_profile(profile: dict) -> None:
    path = CONFIG_DIR / "models_profile.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)


def load_user_profile() -> dict:
    path = DATA_DIR / "memory" / "user_profile.json"
    if not path.exists():
        default = {
            "natural_language_summary": "",
            "dimensions": {
                "communication_style": {"language": "", "tone": "", "detail_level": "", "dislikes": []},
                "output_preferences": {"format": "", "code_comment_language": "", "preferred_stack": [], "file_structure": ""},
                "workflow_preferences": {"interruption_tolerance": "", "review_style": "", "task_granularity": ""},
                "quality_sensitivity": {"high_sensitivity_areas": [], "low_sensitivity_areas": [], "known_rework_triggers": []},
            },
            "interaction_history_summary": "",
            "last_updated": "",
            "update_count": 0,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, indent=2, ensure_ascii=False)
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_user_profile(profile: dict) -> None:
    path = DATA_DIR / "memory" / "user_profile.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)
