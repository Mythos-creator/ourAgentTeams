"""Tests for agents CLI subcommand helpers."""

from __future__ import annotations

import json
from pathlib import Path

from src.cli.agents_cmd import cmd_list, cmd_validate


def _w(p: Path, content: str) -> None:
    p.write_text(content, encoding="utf-8")


GOOD = """---
name: coder
description: writes code
required_skills: [coding, debugging]
preferred_models: [gpt-4o]
---
You are a careful coder. Think step by step before writing.
"""


def test_cmd_list_text(tmp_path: Path):
    _w(tmp_path / "coder.md", GOOD)
    out = cmd_list(tmp_path)
    assert "coder" in out
    assert "coding" in out


def test_cmd_list_json(tmp_path: Path):
    _w(tmp_path / "coder.md", GOOD)
    out = cmd_list(tmp_path, as_json=True)
    data = json.loads(out)
    assert isinstance(data, list)
    assert any(a["name"] == "coder" for a in data)


def test_cmd_list_empty(tmp_path: Path):
    out = cmd_list(tmp_path)
    assert "no agents" in out


def test_cmd_validate_all_ok(tmp_path: Path):
    _w(tmp_path / "coder.md", GOOD)
    code, _ = cmd_validate(tmp_path)
    assert code == 0


def test_cmd_validate_with_error(tmp_path: Path):
    _w(tmp_path / "bad.md", "no frontmatter at all")
    code, text = cmd_validate(tmp_path)
    assert code == 1
    assert "ERROR" in text
