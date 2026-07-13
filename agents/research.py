from __future__ import annotations

from typing import Any, List

from agents.base import BaseAgent
from agents.worker import WorkerMixin
from models.task import Task


class ResearchAgent(WorkerMixin, BaseAgent):
    """
    Handles research-oriented tasks such as information gathering,
    web lookups, and summarising external sources.
    """

    capabilities: List[str] = ["research", "web", "summarise"]

    def execute(self, task: Task) -> Any:
        """
        Execute a research task.

        Placeholder implementation — real logic (LLM calls, web search,
        etc.) will be added in a later milestone.
        """
        return f"[ResearchAgent] Executed: {task.description}"
