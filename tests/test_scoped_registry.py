"""Tests for ScopedToolRegistry (per-agent MCP whitelist)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from src.mcp.scoped_registry import ScopedToolRegistry
from src.mcp.server import MCPToolRegistry


def _registry(tmp: Path) -> MCPToolRegistry:
    return MCPToolRegistry(workspace_root=str(tmp))


def test_none_means_pass_through():
    with tempfile.TemporaryDirectory() as td:
        base = _registry(Path(td))
        view = ScopedToolRegistry(base, allowed=None)
        names = {t["name"] for t in view.list_tools()}
        assert names == {t["name"] for t in base.list_tools()}


def test_empty_list_blocks_everything():
    with tempfile.TemporaryDirectory() as td:
        base = _registry(Path(td))
        view = ScopedToolRegistry(base, allowed=[])
        assert view.list_tools() == []
        assert view.is_allowed("read_file") is False
        out = view.invoke("read_file", path="x")
        assert "not allowed" in out


def test_subset_filtering():
    with tempfile.TemporaryDirectory() as td:
        base = _registry(Path(td))
        view = ScopedToolRegistry(base, allowed=["read_file", "list_directory"])
        names = {t["name"] for t in view.list_tools()}
        assert names == {"read_file", "list_directory"}

        # disallowed → blocked at the view layer
        msg = view.invoke("run_command", command="echo hi")
        assert "not allowed" in msg


def test_get_tools_description_lists_only_allowed():
    with tempfile.TemporaryDirectory() as td:
        base = _registry(Path(td))
        view = ScopedToolRegistry(base, allowed=["read_file"])
        desc = view.get_tools_description()
        assert "read_file" in desc
        assert "run_command" not in desc


def test_invoke_passes_through_when_allowed(tmp_path: Path):
    f = tmp_path / "hello.txt"
    f.write_text("world", encoding="utf-8")
    base = MCPToolRegistry(workspace_root=str(tmp_path))
    view = ScopedToolRegistry(base, allowed=["read_file"])
    out = view.invoke("read_file", path="hello.txt")
    assert "world" in out
