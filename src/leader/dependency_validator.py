"""Dependency validation: detect cycles and validate dependency graph integrity.

Prevents deadlock scenarios where circular dependencies cause tasks to wait forever.
"""

from __future__ import annotations

from typing import Any


class DependencyCycleError(Exception):
    """Raised when a circular dependency is detected."""
    pass


class DependencyValidationError(Exception):
    """Raised when dependency validation fails."""
    pass


def detect_cycle(subtasks: list[dict[str, Any]]) -> list[str] | None:
    """Detect circular dependencies using DFS.
    
    Args:
        subtasks: List of subtask dicts, each with 'id' and 'depends_on' fields.
        
    Returns:
        List of subtask IDs forming a cycle, or None if no cycle exists.
        
    Raises:
        DependencyCycleError: If a cycle is found.
    """
    # Build adjacency map
    graph: dict[str, list[str]] = {}
    all_ids = set()
    
    for st in subtasks:
        sid = st.get("id")
        deps = st.get("depends_on", [])
        if sid:
            graph[sid] = deps
            all_ids.add(sid)
    
    # DFS to find cycle
    visited: set[str] = set()
    rec_stack: set[str] = set()
    
    def _dfs(node: str, path: list[str]) -> list[str] | None:
        visited.add(node)
        rec_stack.add(node)
        path.append(node)
        
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                result = _dfs(neighbor, path[:])
                if result:
                    return result
            elif neighbor in rec_stack:
                # Found cycle: neighbor is in current path
                cycle_start = path.index(neighbor)
                cycle = path[cycle_start:] + [neighbor]
                raise DependencyCycleError(
                    f"Circular dependency detected: {' → '.join(cycle)}"
                )
        
        rec_stack.remove(node)
        return None
    
    # Check all nodes
    for node in all_ids:
        if node not in visited:
            try:
                _dfs(node, [])
            except DependencyCycleError:
                raise
    
    return None


def validate_dependencies(subtasks: list[dict[str, Any]]) -> None:
    """Validate that all dependencies exist and there are no cycles.
    
    Args:
        subtasks: List of subtask dicts.
        
    Raises:
        DependencyCycleError: If a cycle exists.
        DependencyValidationError: If a dependency references non-existent subtask.
    """
    all_ids = {st.get("id") for st in subtasks if st.get("id")}
    
    for st in subtasks:
        deps = st.get("depends_on", [])
        for dep_id in deps:
            if dep_id not in all_ids:
                raise DependencyValidationError(
                    f"Subtask '{st.get('id')}' depends on non-existent task '{dep_id}'"
                )
    
    # Check for cycles
    detect_cycle(subtasks)


def topological_sort(subtasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort subtasks in topological order based on dependencies.
    
    Returns subtasks ordered such that each subtask comes after all its dependencies.
    
    Args:
        subtasks: List of subtask dicts with 'id' and 'depends_on' fields.
        
    Returns:
        Sorted list of subtasks.
        
    Raises:
        DependencyValidationError: If dependencies are invalid or cycles exist.
    """
    validate_dependencies(subtasks)
    
    # Build graph and in-degree map
    graph: dict[str, list[str]] = {}
    in_degree: dict[str, int] = {}
    subtask_map: dict[str, dict[str, Any]] = {}
    
    for st in subtasks:
        sid = st.get("id")
        if sid:
            graph[sid] = []
            in_degree[sid] = 0
            subtask_map[sid] = st
    
    # Build edges: if A depends on B, add B -> A
    for st in subtasks:
        sid = st.get("id")
        for dep_id in st.get("depends_on", []):
            if dep_id in graph:
                graph[dep_id].append(sid)
                in_degree[sid] += 1
    
    # Kahn's algorithm
    queue = [sid for sid, degree in in_degree.items() if degree == 0]
    result = []
    
    while queue:
        node = queue.pop(0)
        result.append(subtask_map[node])
        
        for neighbor in graph.get(node, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)
    
    if len(result) != len(subtasks):
        raise DependencyValidationError("Unable to sort: unresolvable dependencies")
    
    return result


def get_ready_tasks(
    subtasks: list[dict[str, Any]],
    completed_ids: set[str],
) -> list[dict[str, Any]]:
    """Get all subtasks that are ready to execute (dependencies satisfied).
    
    Args:
        subtasks: All subtasks.
        completed_ids: Set of already-completed subtask IDs.
        
    Returns:
        List of ready subtasks (pending status + deps completed).
    """
    ready = []
    for st in subtasks:
        status = st.get("status", "pending")
        if status == "completed":
            continue
        
        deps = st.get("depends_on", [])
        if all(dep_id in completed_ids for dep_id in deps):
            ready.append(st)
    
    return ready
