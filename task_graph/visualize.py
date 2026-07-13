from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from task_graph.node import TaskNode
from task_graph.state import NodeState


class GraphVisualizer(ABC):
    """
    Strategy for rendering a set of :class:`TaskNode`\\ s to a string.

    Kept separate from the graph so that (a) the core engine carries no
    diagramming concern, and (b) render formats (Mermaid, Graphviz DOT, ASCII)
    are swappable without touching the graph.
    """

    @abstractmethod
    def render(self, nodes: Sequence[TaskNode]) -> str:
        """Return a textual rendering of the DAG described by *nodes*."""


class MermaidVisualizer(GraphVisualizer):
    """
    Render the DAG as a Mermaid ``graph TD`` block (GitHub-native).

    Nodes are labelled with a short id + description and CSS-classed by state so
    the diagram doubles as a live status view.
    """

    _CLASS_BY_STATE = {
        NodeState.BLOCKED: "blocked",
        NodeState.READY: "ready",
        NodeState.RUNNING: "running",
        NodeState.COMPLETED: "done",
        NodeState.FAILED: "failed",
        NodeState.CANCELLED: "cancelled",
    }

    _CLASS_DEFS = (
        "classDef blocked fill:#eee,stroke:#999;",
        "classDef ready fill:#e6f0ff,stroke:#3b82f6;",
        "classDef running fill:#fff7e6,stroke:#f59e0b;",
        "classDef done fill:#e6ffed,stroke:#22c55e;",
        "classDef failed fill:#ffe6e6,stroke:#ef4444;",
        "classDef cancelled fill:#f3f3f3,stroke:#bbb,stroke-dasharray:3;",
    )

    def render(self, nodes: Sequence[TaskNode]) -> str:
        lines: list[str] = ["graph TD"]

        for node in nodes:
            nid = self._nid(node.task_id)
            label = self._escape(node.description)
            lines.append(f'    {nid}["{label}"]')

        for node in nodes:
            for dep in node.dependencies:
                # Edge points parent -> child (execution order).
                lines.append(f"    {self._nid(dep)} --> {self._nid(node.task_id)}")

        for node in nodes:
            cls = self._CLASS_BY_STATE[node.state]
            lines.append(f"    class {self._nid(node.task_id)} {cls};")

        lines.extend(f"    {d}" for d in self._CLASS_DEFS)
        return "\n".join(lines)

    @staticmethod
    def _nid(task_id) -> str:
        # Mermaid node ids must be identifier-safe; hex of the uuid works.
        return "n" + str(task_id).replace("-", "")[:12]

    @staticmethod
    def _escape(text: str) -> str:
        return text.replace('"', "'")[:60]
