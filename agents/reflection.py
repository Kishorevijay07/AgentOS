from __future__ import annotations

from typing import Any, List, Optional

from agents.base import BaseAgent
from agents.worker import WorkerMixin
from models.result import AgentResult
from models.task import Task


class ReflectionAgent(WorkerMixin, BaseAgent):
    """
    Evaluates the output quality of a completed task.

    The ReflectionAgent never re-does the work itself.  When quality is
    deemed insufficient it creates a **new** corrective ``Task`` and returns
    it so the caller can push it back into the ``TaskQueue``.  This avoids
    immediate retries and keeps the queue as the single source of truth.

    Quality heuristic
    -----------------
    The current implementation checks whether the output string is at least
    ``_MIN_OUTPUT_LENGTH`` characters long.  A future milestone will swap
    this for an LLM-based rubric without changing the public interface.
    """

    capabilities: List[str] = ["reflect", "evaluate"]

    _MIN_OUTPUT_LENGTH: int = 10
    """Minimum character count for an output to be considered acceptable."""

    def execute(self, task: Task) -> Optional[Task]:
        """
        Evaluate ``task.result`` and return a corrective task if needed.

        Parameters
        ----------
        task:
            A completed ``Task`` whose ``result`` field holds the output.

        Returns
        -------
        Task | None
            A new corrective ``Task`` if quality is poor, otherwise ``None``.
        """
        output = task.result or ""
        if self._is_acceptable(output):
            return None
        return self._make_retry_from_task(task, reason="Output quality below threshold.")

    def evaluate_result(self, agent_result: AgentResult) -> Optional[Task]:
        """
        Evaluate an ``AgentResult`` directly from the ResultQueue.

        This is the preferred entry point inside the LangGraph reflection
        node, where results arrive as ``AgentResult`` objects rather than
        completed ``Task`` objects.

        Returns
        -------
        Task | None
            A new corrective ``Task`` if quality is poor, otherwise ``None``.
        """
        if not agent_result.success:
            reason = agent_result.error or "Agent execution failed."
            return self._make_retry_from_result(agent_result, reason)

        output = str(agent_result.output or "")
        if not self._is_acceptable(output):
            return self._make_retry_from_result(
                agent_result, reason="Output quality below threshold."
            )
        return None

    # ------------------------------------------------------------------ #
    #  Private helpers                                                    #
    # ------------------------------------------------------------------ #

    def _is_acceptable(self, output: str) -> bool:
        """Return ``True`` when the output meets the minimum quality bar."""
        return len(output.strip()) >= self._MIN_OUTPUT_LENGTH

    def _make_retry_from_task(self, original: Task, reason: str) -> Task:
        """Build a corrective task from a ``Task`` object."""
        return Task(
            description=f"[RETRY] {original.description} — {reason}",
            priority=original.priority,
            required_capabilities=original.required_capabilities,
        )

    def _make_retry_from_result(self, result: AgentResult, reason: str) -> Task:
        """Build a corrective task from an ``AgentResult`` object."""
        return Task(
            description=f"[RETRY] agent={result.agent_name} task={result.task_id} — {reason}",
            required_capabilities=[],
        )
