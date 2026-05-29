"""Markdown report generator for an eval run.

Reads a JSON report produced by `eval.runner.save_report` and renders a
human-friendly Markdown summary.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def render_report(payload: dict) -> str:
    lines: list[str] = []
    bench = payload.get("benchmark", "?")
    cands = payload.get("candidates", [])
    lines.append(f"# Eval Report — {bench}")
    lines.append("")
    lines.append(f"Candidates: {', '.join(cands) or '(none)'}")
    lines.append("")

    # Leaderboard
    lb = payload.get("leaderboard", [])
    lines.append("## Leaderboard (ELO)")
    lines.append("")
    lines.append("| Rank | Candidate | Rating | Games |")
    lines.append("|------|-----------|--------|-------|")
    for i, row in enumerate(lb, 1):
        lines.append(f"| {i} | {row['name']} | {row['rating']:.1f} | {row['games']} |")
    lines.append("")

    # Win/loss matrix
    matches = payload.get("matches", [])
    pair_stats: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"a_win": 0, "b_win": 0, "tie": 0})
    for m in matches:
        key = (m["a"], m["b"])
        w = m.get("winner", "tie")
        if w == "A" or w == m["a"]:
            pair_stats[key]["a_win"] += 1
        elif w == "B" or w == m["b"]:
            pair_stats[key]["b_win"] += 1
        else:
            pair_stats[key]["tie"] += 1

    if pair_stats:
        lines.append("## Pairwise Outcomes")
        lines.append("")
        lines.append("| A | B | A wins | B wins | Tie |")
        lines.append("|---|---|--------|--------|-----|")
        for (a, b), s in pair_stats.items():
            lines.append(f"| {a} | {b} | {s['a_win']} | {s['b_win']} | {s['tie']} |")
        lines.append("")

    # Per-item snippets
    answers = payload.get("answers", [])
    by_item: dict[str, list[dict]] = defaultdict(list)
    for a in answers:
        by_item[a["item_id"]].append(a)

    if by_item:
        lines.append("## Sample Answers")
        lines.append("")
        for item_id, ans_list in by_item.items():
            lines.append(f"### {item_id}")
            for a in ans_list:
                snippet = (a.get("answer") or "").strip().replace("\n", " ")
                if len(snippet) > 240:
                    snippet = snippet[:240] + "..."
                err = a.get("error", "")
                tag = f"  *(error: {err})*" if err else ""
                lines.append(f"- **{a['candidate']}**{tag}: {snippet or '(empty)'}")
            lines.append("")

    return "\n".join(lines)


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="eval.report", description="Render an eval JSON report as Markdown.")
    p.add_argument("input", help="Path to JSON report (from eval.runner)")
    p.add_argument("--out", default=None, help="Output Markdown path (defaults to <input>.md)")
    return p


def main() -> None:
    args = _build_argparser().parse_args()
    src = Path(args.input)
    payload = json.loads(src.read_text(encoding="utf-8"))
    md = render_report(payload)
    out = Path(args.out) if args.out else src.with_suffix(".md")
    out.write_text(md, encoding="utf-8")
    print(f"Markdown written to: {out}")


if __name__ == "__main__":
    main()
