from __future__ import annotations

from typing import Any, List

from agents.base import BaseAgent
from agents.worker import WorkerMixin
from models.task import Task


class CodingAgent(WorkerMixin, BaseAgent):
    """
    Handles code-generation tasks such as implementing features,
    writing boilerplate, and refactoring existing code.
    """

    capabilities: List[str] = ["code", "implement", "refactor", "debug"]

    def execute(self, task: Task) -> Any:
        """
        Execute a coding task.

        Placeholder implementation — real logic (LLM-driven code generation,
        etc.) will be added in a later milestone.
        """
        return f"[CodingAgent] Executed: {task.description}"
