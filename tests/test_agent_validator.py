"""Tests for agent frontmatter validator."""

from __future__ import annotations

from pathlib import Path

from src.leader.agent_validator import (
    format_report, validate_agent_md, validate_agents_dir,
)


def _w(p: Path, content: str) -> Path:
    p.write_text(content, encoding="utf-8")
    return p


def test_valid_agent_passes(tmp_path: Path):
    p = _w(tmp_path / "good.md", """---
name: coder
description: writes code
required_skills: [coding]
temperature: 0.2
max_tokens: 2048
plan_mode: auto
---
You are a careful, precise coder. Always think before you write.
""")
    r = validate_agent_md(p)
    assert r.ok
    assert r.errors() == []


def test_missing_frontmatter():
    # use a real file: write to tmp
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write("just a body, no frontmatter")
        path = Path(f.name)
    try:
        r = validate_agent_md(path)
        assert not r.ok
        assert any("frontmatter" in i.message for i in r.errors())
    finally:
        path.unlink()


def test_required_fields_missing(tmp_path: Path):
    p = _w(tmp_path / "bad.md", """---
description: missing name
---
Body here that is long enough.
""")
    r = validate_agent_md(p)
    assert not r.ok
    assert any(i.field == "name" for i in r.errors())


def test_temperature_out_of_range_is_warning(tmp_path: Path):
    p = _w(tmp_path / "warm.md", """---
name: hot
description: weird
temperature: 5.0
---
Long enough body for system prompt usage scenario.
""")
    r = validate_agent_md(p)
    assert r.ok  # warning, not error
    assert any(i.field == "temperature" and i.severity == "warning" for i in r.warnings())


def test_plan_mode_invalid_is_error(tmp_path: Path):
    p = _w(tmp_path / "pm.md", """---
name: a
description: b
plan_mode: sometimes
---
Long enough body.
""")
    r = validate_agent_md(p)
    assert not r.ok
    assert any(i.field == "plan_mode" for i in r.errors())


def test_list_field_must_be_list(tmp_path: Path):
    p = _w(tmp_path / "ls.md", """---
name: a
description: b
preferred_models: gpt-4o
---
Long enough body.
""")
    r = validate_agent_md(p)
    assert not r.ok
    assert any(i.field == "preferred_models" for i in r.errors())


def test_unknown_field_is_warning(tmp_path: Path):
    p = _w(tmp_path / "unk.md", """---
name: a
description: b
foo_bar: 1
---
Long enough body for testing the validator now.
""")
    r = validate_agent_md(p)
    assert r.ok
    assert any(i.field == "foo_bar" and i.severity == "warning" for i in r.warnings())


def test_validate_agents_dir_returns_empty_for_missing(tmp_path: Path):
    assert validate_agents_dir(tmp_path / "nope") == []


def test_format_report_summary(tmp_path: Path):
    _w(tmp_path / "good.md", """---
name: ok
description: fine
---
A reasonably long system prompt body here.
""")
    _w(tmp_path / "bad.md", "no frontmatter")
    results = validate_agents_dir(tmp_path)
    rep = format_report(results)
    assert "共检查 2" in rep
    assert "error" in rep
