from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, List

from models.task import Task


class BaseAgent(ABC):
    """
    Abstract base class for all worker agents.

    Every concrete agent must declare its ``capabilities`` — a list of
    lowercase strings that describe what kinds of tasks the agent can handle.
    The Scheduler uses these capabilities to route tasks without ever
    hard-coding task-name checks.
    """

    # Subclasses must override this with their own capability list.
    capabilities: List[str] = []

    @abstractmethod
    def execute(self, task: Task) -> Any:
        """
        Execute a task and return the result.

        Parameters
        ----------
        task:
            The ``Task`` object assigned by the Scheduler.

        Returns
        -------
        Any
            An arbitrary result value; the Scheduler will store this in
            ``task.result`` after the call completes.
        """
