"""Eval runner: load benchmarks, query candidate models, run pairwise judging.

Self-contained — does not depend on the Leader / orchestrator pipeline.
Workers are constructed directly from AppConfig.

Usage example::

    python -m eval.runner --bench coding --candidates qwen2.5:14b llama3.1:8b
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
from dataclasses import asdict, dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any

import yaml

from src.config import AppConfig, WorkerEntry, load_config
from src.leader.agent_registry import AgentProfile, AgentRegistry
from src.models.api_model import APIModelWorker
from src.models.base import BaseModelWorker, ModelResponse
from src.models.local_model import OllamaWorker

from .elo import EloTable, winner_to_score


_ROOT = Path(__file__).resolve().parent
BENCH_DIR = _ROOT / "benchmarks"
JUDGE_PATH = _ROOT / "judges" / "pairwise.md"
RESULTS_DIR = _ROOT / "results"


# ── Data structures ─────────────────────────────────────────────────────────


@dataclass
class BenchmarkItem:
    id: str
    title: str
    prompt: str
    reference: str = ""


@dataclass
class Benchmark:
    name: str
    description: str
    required_skills: list[str]
    items: list[BenchmarkItem]


@dataclass
class CandidateAnswer:
    candidate: str
    item_id: str
    answer: str
    elapsed_s: float = 0.0
    tokens: int = 0
    error: str = ""


@dataclass
class JudgedMatch:
    item_id: str
    a: str
    b: str
    winner: str
    reason: str


@dataclass
class EvalReport:
    benchmark: str
    candidates: list[str]
    answers: list[CandidateAnswer] = field(default_factory=list)
    matches: list[JudgedMatch] = field(default_factory=list)
    leaderboard: list[tuple[str, float, int]] = field(default_factory=list)


# ── Loading ────────────────────────────────────────────────────────────────


def load_benchmark(name: str) -> Benchmark:
    path = BENCH_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"benchmark not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    items = [
        BenchmarkItem(
            id=it["id"],
            title=it.get("title", it["id"]),
            prompt=it["prompt"],
            reference=it.get("reference", ""),
        )
        for it in raw.get("items", [])
    ]
    return Benchmark(
        name=raw.get("name", name),
        description=raw.get("description", ""),
        required_skills=raw.get("required_skills", []),
        items=items,
    )


def list_benchmarks() -> list[str]:
    if not BENCH_DIR.exists():
        return []
    return sorted(p.stem for p in BENCH_DIR.glob("*.yaml"))


def load_judge_prompt() -> str:
    if not JUDGE_PATH.exists():
        raise FileNotFoundError(f"judge prompt not found: {JUDGE_PATH}")
    return JUDGE_PATH.read_text(encoding="utf-8")


# ── Worker construction ────────────────────────────────────────────────────


def _find_worker_entry(cfg: AppConfig, model: str) -> WorkerEntry | None:
    for w in cfg.workers_local + cfg.workers_api:
        if w.model == model:
            return w
    return None


def build_worker(cfg: AppConfig, model: str) -> BaseModelWorker:
    """Construct a BaseModelWorker for `model` based on AppConfig.

    Falls back to OllamaWorker when the model isn't in config (so eval can
    target ad-hoc local models pulled via `ollama pull`).
    """
    entry = _find_worker_entry(cfg, model)
    if entry is None or entry.provider == "ollama":
        return OllamaWorker(model=model, base_url=cfg.leader.ollama_base_url)
    return APIModelWorker(model=model, api_key=entry.api_key)


def resolve_agent_model(profile: AgentProfile, cfg: AppConfig) -> str | None:
    return profile.resolve_model(cfg)


# ── Generation phase ───────────────────────────────────────────────────────


async def _gen_one(
    worker: BaseModelWorker,
    item: BenchmarkItem,
    *,
    system_prompt: str = "",
    temperature: float = 0.3,
    max_tokens: int = 2048,
) -> CandidateAnswer:
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": item.prompt})
    try:
        resp: ModelResponse = await worker.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return CandidateAnswer(
            candidate=worker.model,
            item_id=item.id,
            answer=resp.content,
            elapsed_s=resp.elapsed_s,
            tokens=resp.total_tokens,
        )
    except Exception as exc:  # don't crash the whole run on one failure
        return CandidateAnswer(
            candidate=worker.model,
            item_id=item.id,
            answer="",
            error=str(exc),
        )


async def gen_answers(
    workers: dict[str, BaseModelWorker],
    benchmark: Benchmark,
    *,
    system_prompts: dict[str, str] | None = None,
) -> list[CandidateAnswer]:
    """Generate answers for every (candidate, item) pair sequentially per item."""
    sys_prompts = system_prompts or {}
    out: list[CandidateAnswer] = []
    for item in benchmark.items:
        tasks = [
            _gen_one(w, item, system_prompt=sys_prompts.get(name, ""))
            for name, w in workers.items()
        ]
        out.extend(await asyncio.gather(*tasks))
    return out


# ── Judging phase ──────────────────────────────────────────────────────────


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_judge(text: str) -> tuple[str, str]:
    """Extract winner + reason from judge response. Tolerant to fences/prose."""
    m = _JSON_BLOCK_RE.search(text)
    if not m:
        return "tie", "judge_unparsable"
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return "tie", "judge_invalid_json"
    winner = str(data.get("winner", "tie"))
    reason = str(data.get("reason", ""))
    return winner, reason


async def judge_pair(
    judge: BaseModelWorker,
    judge_prompt: str,
    item: BenchmarkItem,
    a_name: str,
    a_answer: str,
    b_name: str,
    b_answer: str,
) -> JudgedMatch:
    user_msg = (
        f"## 任务\n{item.prompt}\n\n"
        f"## 参考要点\n{item.reference or '（无）'}\n\n"
        f"## 候选 A\n{a_answer or '（空）'}\n\n"
        f"## 候选 B\n{b_answer or '（空）'}\n"
    )
    try:
        resp = await judge.chat(
            messages=[
                {"role": "system", "content": judge_prompt},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.0,
            max_tokens=512,
        )
        winner, reason = _parse_judge(resp.content)
    except Exception as exc:
        winner, reason = "tie", f"judge_error: {exc}"
    return JudgedMatch(item_id=item.id, a=a_name, b=b_name, winner=winner, reason=reason)


async def run_pairwise(
    judge: BaseModelWorker,
    benchmark: Benchmark,
    answers: list[CandidateAnswer],
    *,
    swap_sides: bool = True,
    seed: int = 0,
) -> tuple[list[JudgedMatch], EloTable]:
    """For each item, judge every pair of candidates; update an ELO table.

    swap_sides=True runs each pair twice (A/B then B/A) to mitigate position bias.
    """
    judge_prompt = load_judge_prompt()
    by_item: dict[str, dict[str, str]] = {}
    for ans in answers:
        by_item.setdefault(ans.item_id, {})[ans.candidate] = ans.answer

    rng = random.Random(seed)
    matches: list[JudgedMatch] = []
    elo = EloTable()

    for item in benchmark.items:
        slot = by_item.get(item.id, {})
        names = list(slot.keys())
        rng.shuffle(names)
        for a, b in combinations(names, 2):
            m1 = await judge_pair(judge, judge_prompt, item, a, slot[a], b, slot[b])
            matches.append(m1)
            elo.update(a, b, winner_to_score(m1.winner, a, b))
            if swap_sides:
                m2 = await judge_pair(judge, judge_prompt, item, b, slot[b], a, slot[a])
                # In m2, "A" position is `b`, so winner_to_score's score_for_a is for `b`
                score_for_b = winner_to_score(m2.winner, b, a)
                # convert to score_for_a (the original `a`)
                elo.update(a, b, 1.0 - score_for_b)
                matches.append(JudgedMatch(item_id=item.id, a=b, b=a, winner=m2.winner, reason=m2.reason))

    return matches, elo


# ── Top-level orchestration ────────────────────────────────────────────────


async def run_eval(
    bench_name: str,
    candidates: list[str],
    *,
    judge_model: str | None = None,
    cfg: AppConfig | None = None,
    use_agents: bool = False,
    swap_sides: bool = True,
) -> EvalReport:
    cfg = cfg or load_config()
    benchmark = load_benchmark(bench_name)

    # Optionally treat `candidates` as agent names instead of raw model ids.
    sys_prompts: dict[str, str] = {}
    workers: dict[str, BaseModelWorker] = {}
    if use_agents:
        registry = AgentRegistry()
        for name in candidates:
            ap = registry.get(name)
            if ap is None:
                raise ValueError(f"agent not found: {name}")
            model = ap.resolve_model(cfg)
            if model is None:
                raise ValueError(f"agent {name} has no resolvable model in current config")
            workers[name] = build_worker(cfg, model)
            sys_prompts[name] = ap.system_prompt
    else:
        for model in candidates:
            workers[model] = build_worker(cfg, model)

    answers = await gen_answers(workers, benchmark, system_prompts=sys_prompts)

    judge_id = judge_model or cfg.leader.model
    judge = build_worker(cfg, judge_id)
    matches, elo = await run_pairwise(judge, benchmark, answers, swap_sides=swap_sides)

    return EvalReport(
        benchmark=benchmark.name,
        candidates=list(workers.keys()),
        answers=answers,
        matches=matches,
        leaderboard=elo.leaderboard(),
    )


def save_report(report: EvalReport, dest: Path | None = None) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    dest = dest or (RESULTS_DIR / f"{report.benchmark}.json")
    payload: dict[str, Any] = {
        "benchmark": report.benchmark,
        "candidates": report.candidates,
        "answers": [asdict(a) for a in report.answers],
        "matches": [asdict(m) for m in report.matches],
        "leaderboard": [
            {"name": name, "rating": rating, "games": games}
            for name, rating, games in report.leaderboard
        ],
    }
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


# ── CLI ────────────────────────────────────────────────────────────────────


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="eval.runner", description="Run offline pairwise evaluation.")
    p.add_argument("--bench", required=True, help=f"benchmark name (one of: {', '.join(list_benchmarks()) or 'none found'})")
    p.add_argument("--candidates", nargs="+", required=True, help="model ids OR agent names if --agents")
    p.add_argument("--judge", default=None, help="judge model id (defaults to leader.model)")
    p.add_argument("--agents", action="store_true", help="treat --candidates as agent names from agents/*.md")
    p.add_argument("--no-swap", action="store_true", help="disable A/B swap for position-bias mitigation")
    p.add_argument("--out", default=None, help="output JSON path")
    return p


def _print_summary(report: EvalReport) -> None:
    print(f"\n=== {report.benchmark} ===")
    print(f"candidates: {', '.join(report.candidates)}")
    print(f"answers: {len(report.answers)}  matches: {len(report.matches)}")
    print("\nleaderboard:")
    for name, rating, games in report.leaderboard:
        print(f"  {name:30s}  rating={rating:7.1f}  games={games}")


def main() -> None:
    args = _build_argparser().parse_args()
    report = asyncio.run(run_eval(
        bench_name=args.bench,
        candidates=args.candidates,
        judge_model=args.judge,
        use_agents=args.agents,
        swap_sides=not args.no_swap,
    ))
    out_path = save_report(report, Path(args.out) if args.out else None)
    _print_summary(report)
    print(f"\nreport saved to: {out_path}")


if __name__ == "__main__":
    main()
