"""Lightweight watchdog that monitors leader heartbeat and triggers resume.

Usage:
  python -m src.watchdog --session <session_id|latest>
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

from src.config import DATA_DIR, load_config
from src.leader.monitor import check_leader_alive


def _latest_session_id() -> str | None:
    sdir = DATA_DIR / "sessions"
    if not sdir.exists():
        return None
    candidates = [p for p in sdir.glob("sess_*") if (p / "state.json").exists()]
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0].name


def main() -> None:
    parser = argparse.ArgumentParser(description="Leader watchdog")
    parser.add_argument("--session", default="latest", help="session id to resume when failure detected")
    args = parser.parse_args()

    cfg = load_config()
    timeout = max(int(cfg.leader.watchdog_timeout_s), 5)
    interval = max(timeout // 2, 3)

    while True:
        time.sleep(interval)
        if check_leader_alive(timeout):
            continue

        sid = args.session if args.session != "latest" else _latest_session_id()
        if not sid:
            continue

        subprocess.Popen(
            [sys.executable, "-m", "src.cli.main", "resume", sid],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return


if __name__ == "__main__":
    main()
