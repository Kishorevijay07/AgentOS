from __future__ import annotations

from typing import Any, List, Tuple

from agents.base import BaseAgent
from agents.worker import WorkerMixin
from models.task import Task


class PlannerAgent(WorkerMixin, BaseAgent):
    """
    Decomposes a high-level goal string into a concrete list of Tasks.

    The Planner never executes domain work itself.  It only produces a
    breakdown of subtasks that the caller (or the LangGraph planner node)
    pushes into the ``TaskQueue``.

    Each subtask carries ``required_capabilities`` so the Scheduler can
    route it without ever inspecting the description text.

    A future milestone will replace ``_default_steps`` with an LLM-driven
    decomposition.  The public contract (``execute`` → ``List[Task]``) stays
    unchanged.
    """

    capabilities: List[str] = ["plan", "decompose"]

    def execute(self, task: Task) -> List[Task]:
        """
        Decompose the task's ``description`` (treated as a goal) into subtasks.

        Parameters
        ----------
        task:
            A top-level ``Task`` whose ``description`` is the natural-language
            goal, e.g. ``"Build REST API"``.

        Returns
        -------
        List[Task]
            Ordered subtasks ready to be enqueued.
        """
        return self._decompose_goal(task.description)

    # ------------------------------------------------------------------ #
    #  Private helpers                                                    #
    # ------------------------------------------------------------------ #

    def _decompose_goal(self, goal: str) -> List[Task]:
        """
        Map a goal string to an ordered list of Tasks.

        Each element in ``_default_steps`` is a ``(description, capabilities)``
        pair; this method converts each pair into a ``Task``.
        """
        steps = self._default_steps(goal)
        return [self._make_task(desc, caps) for desc, caps in steps]

    def _default_steps(self, goal: str) -> List[Tuple[str, List[str]]]:
        """
        Return the canonical four-step plan for any goal.

        Override this method in subclasses to implement domain-specific
        decomposition strategies.

        Returns
        -------
        List[Tuple[str, List[str]]]
            Each tuple is ``(task_description, required_capabilities)``.
        """
        return [
            (f"Research requirements for: {goal}", ["research", "web"]),
            (f"Implement solution for: {goal}", ["code", "implement"]),
            (f"Write tests for: {goal}", ["test", "qa"]),
            (f"Document the implementation of: {goal}", ["document", "docstring"]),
        ]

    def _make_task(self, description: str, capabilities: List[str]) -> Task:
        """Construct a ``Task`` with the given description and required capabilities."""
        return Task(description=description, required_capabilities=capabilities)
