from __future__ import annotations

from typing import Any, List

from agents.base import BaseAgent
from agents.worker import WorkerMixin
from models.task import Task


class DocumentationAgent(WorkerMixin, BaseAgent):
    """
    Handles documentation tasks such as writing docstrings, README files,
    API references, and user guides.
    """

    capabilities: List[str] = ["document", "docstring", "readme", "explain"]

    def execute(self, task: Task) -> Any:
        """
        Execute a documentation task.

        Placeholder implementation — real logic (doc generation, etc.)
        will be added in a later milestone.
        """
        return f"[DocumentationAgent] Executed: {task.description}"
