"""MCP tool registry: exposes filesystem, search, and code execution tools
that the Leader can invoke during task analysis or integration.

These tools are injected into the Leader's context so it can interact
with the local environment when needed (read project files, search code,
run scripts, etc.).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., str]


class MCPToolRegistry:
    """Manages available tools the Leader can call."""

    def __init__(self, workspace_root: str | None = None):
        self._tools: dict[str, ToolDefinition] = {}
        self._workspace = Path(workspace_root) if workspace_root else Path.cwd()
        self._register_builtins()

    def _register_builtins(self) -> None:
        self.register(ToolDefinition(
            name="read_file",
            description="读取指定路径的文件内容",
            parameters={"path": {"type": "string", "description": "文件路径"}},
            handler=self._read_file,
        ))
        self.register(ToolDefinition(
            name="write_file",
            description="将内容写入指定路径的文件",
            parameters={
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "文件内容"},
            },
            handler=self._write_file,
        ))
        self.register(ToolDefinition(
            name="list_directory",
            description="列出目录下的文件和子目录",
            parameters={"path": {"type": "string", "description": "目录路径"}},
            handler=self._list_directory,
        ))
        self.register(ToolDefinition(
            name="search_files",
            description="在目录中搜索匹配关键词的文件内容",
            parameters={
                "pattern": {"type": "string", "description": "搜索关键词"},
                "directory": {"type": "string", "description": "搜索目录", "default": "."},
            },
            handler=self._search_files,
        ))
        self.register(ToolDefinition(
            name="run_command",
            description="执行一条 shell 命令并返回输出（限时30秒）",
            parameters={"command": {"type": "string", "description": "Shell 命令"}},
            handler=self._run_command,
        ))

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in self._tools.values()
        ]

    def invoke(self, name: str, **kwargs: Any) -> str:
        tool = self._tools.get(name)
        if not tool:
            return f"Error: tool '{name}' not found"
        try:
            return tool.handler(**kwargs)
        except Exception as exc:
            return f"Error: {exc}"

    def get_tools_description(self) -> str:
        """Return a human-readable tools list for injection into prompts."""
        lines = ["可用工具:"]
        for t in self._tools.values():
            params = ", ".join(f"{k}: {v.get('type', 'any')}" for k, v in t.parameters.items())
            lines.append(f"  - {t.name}({params}): {t.description}")
        return "\n".join(lines)

    # -- Built-in handlers --

    def _read_file(self, path: str) -> str:
        p = self._resolve(path)
        if not p.exists():
            return f"File not found: {path}"
        try:
            return p.read_text(encoding="utf-8")[:50_000]
        except Exception as e:
            return f"Error reading file: {e}"

    def _write_file(self, path: str, content: str) -> str:
        p = self._resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Written {len(content)} bytes to {path}"

    def _list_directory(self, path: str = ".") -> str:
        p = self._resolve(path)
        if not p.is_dir():
            return f"Not a directory: {path}"
        items = sorted(p.iterdir())
        lines = []
        for item in items[:200]:
            prefix = "[DIR] " if item.is_dir() else "      "
            lines.append(f"{prefix}{item.name}")
        return "\n".join(lines) or "(empty)"

    def _search_files(self, pattern: str, directory: str = ".") -> str:
        p = self._resolve(directory)
        if not p.is_dir():
            return f"Not a directory: {directory}"
        results: list[str] = []
        for fp in p.rglob("*"):
            if fp.is_file() and fp.stat().st_size < 1_000_000:
                try:
                    content = fp.read_text(encoding="utf-8", errors="ignore")
                    for i, line in enumerate(content.splitlines(), 1):
                        if pattern.lower() in line.lower():
                            rel = fp.relative_to(p)
                            results.append(f"{rel}:{i}: {line.strip()}")
                            if len(results) >= 50:
                                return "\n".join(results) + "\n... (truncated)"
                except Exception:
                    continue
        return "\n".join(results) or "No matches found"

    def _run_command(self, command: str) -> str:
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=30, cwd=str(self._workspace),
            )
            output = result.stdout + result.stderr
            return output[:20_000] or "(no output)"
        except subprocess.TimeoutExpired:
            return "Error: command timed out (30s limit)"
        except Exception as e:
            return f"Error: {e}"

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        if p.is_absolute():
            return p
        return self._workspace / p
