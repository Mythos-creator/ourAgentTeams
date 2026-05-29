"""Per-Agent 工具白名单：把全局 MCPToolRegistry 包装成"只暴露指定工具"的视图。

不修改原 registry，便于不同 Agent 拿到不同的子集。
典型用法：

    base = MCPToolRegistry(workspace_root="/proj")
    reviewer_view = ScopedToolRegistry(base, allowed=["read_file", "search_files"])
    reviewer_view.list_tools()           # 只剩 read/search
    reviewer_view.invoke("run_command")  # → "Error: tool not allowed for this agent"
"""

from __future__ import annotations

from typing import Any, Iterable

from src.mcp.server import MCPToolRegistry, ToolDefinition


class ScopedToolRegistry:
    """对底层 MCPToolRegistry 做白名单过滤的视图。"""

    def __init__(self, base: MCPToolRegistry, allowed: Iterable[str] | None) -> None:
        self._base = base
        # None → 不过滤；空 list → 全部禁用；普通 list → 仅这些
        self._allowed: set[str] | None = (
            set(allowed) if allowed is not None else None
        )

    @property
    def base(self) -> MCPToolRegistry:
        return self._base

    def is_allowed(self, name: str) -> bool:
        if self._allowed is None:
            return True
        return name in self._allowed

    def get_tool(self, name: str) -> ToolDefinition | None:
        if not self.is_allowed(name):
            return None
        return self._base.get_tool(name)

    def list_tools(self) -> list[dict[str, Any]]:
        return [t for t in self._base.list_tools() if self.is_allowed(t["name"])]

    def get_tools_description(self) -> str:
        if self._allowed is not None and not self._allowed:
            return "可用工具: (无)"
        lines = ["可用工具:"]
        for t in self._base.list_tools():
            if not self.is_allowed(t["name"]):
                continue
            params = ", ".join(
                f"{k}: {v.get('type', 'any')}" for k, v in t["parameters"].items()
            )
            lines.append(f"  - {t['name']}({params}): {t['description']}")
        if len(lines) == 1:
            return "可用工具: (无)"
        return "\n".join(lines)

    def invoke(self, name: str, **kwargs: Any) -> str:
        if not self.is_allowed(name):
            return f"Error: tool '{name}' not allowed for this agent"
        return self._base.invoke(name, **kwargs)
