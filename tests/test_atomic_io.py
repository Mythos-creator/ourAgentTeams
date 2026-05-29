"""Tests for atomic file IO utils."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from src.utils.atomic_io import (
    atomic_write_json, atomic_write_text, file_lock, update_json_atomically,
)


def test_atomic_write_text_creates_file(tmp_path: Path):
    p = tmp_path / "x.txt"
    atomic_write_text(p, "hello")
    assert p.read_text() == "hello"


def test_atomic_write_text_no_leftover_on_failure(tmp_path: Path, monkeypatch):
    p = tmp_path / "x.txt"

    def boom(*a, **kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr("os.replace", boom)
    try:
        atomic_write_text(p, "data")
    except RuntimeError:
        pass
    leftovers = list(tmp_path.iterdir())
    assert leftovers == []  # tmp file cleaned up


def test_atomic_write_json_writes_unicode(tmp_path: Path):
    p = tmp_path / "data.json"
    atomic_write_json(p, {"name": "张三", "n": 3})
    assert json.loads(p.read_text(encoding="utf-8")) == {"name": "张三", "n": 3}


def test_update_json_atomically_seeds_empty(tmp_path: Path):
    p = tmp_path / "u.json"
    out = update_json_atomically(p, lambda d: {**d, "k": 1})
    assert out == {"k": 1}
    assert json.loads(p.read_text()) == {"k": 1}


def test_update_json_atomically_concurrent(tmp_path: Path):
    p = tmp_path / "u.json"

    def bump():
        for _ in range(50):
            update_json_atomically(p, lambda d: {**d, "n": d.get("n", 0) + 1})

    threads = [threading.Thread(target=bump) for _ in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()

    final = json.loads(p.read_text())
    assert final["n"] == 200  # 4 threads * 50 increments, no lost updates


def test_file_lock_serializes_writers(tmp_path: Path):
    p = tmp_path / "w.txt"
    order: list[str] = []

    def worker(tag: str):
        with file_lock(p):
            order.append(f"{tag}-in")
            # ensure interleavings would show
            for _ in range(1000): pass
            order.append(f"{tag}-out")

    threads = [threading.Thread(target=worker, args=(str(i),)) for i in range(3)]
    for t in threads: t.start()
    for t in threads: t.join()

    # each writer's in/out must be adjacent (no interleaving)
    for i in range(0, len(order), 2):
        a, b = order[i], order[i + 1]
        assert a.endswith("-in") and b.endswith("-out")
        assert a.split("-")[0] == b.split("-")[0]
