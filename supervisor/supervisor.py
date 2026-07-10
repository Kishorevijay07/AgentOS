from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from agents.base import BaseAgent
from models.result import AgentResult
from models.task import Task
from queue.result_queue import ResultQueue
from queue.task_queue import TaskQueue
from scheduler.scheduler import Scheduler


class Supervisor:
    """
    OS-scheduler analogue for the agent system.

    The Supervisor has exactly four responsibilities, nothing more:

    1. **Read** the next pending task from the ``TaskQueue``.
    2. **Choose** the appropriate worker via the ``Scheduler``.
    3. **Assign** the task (records the chosen agent's name on the task).
    4. **Monitor** progress by collecting results from the ``ResultQueue``.

    The Supervisor never implements domain logic itself.  All work is
    delegated to the agent chosen by the Scheduler.
    """

    def __init__(
        self,
        task_queue: TaskQueue,
        scheduler: Scheduler,
        result_queue: ResultQueue,
    ) -> None:
        self._task_queue = task_queue
        self._scheduler = scheduler
        self._result_queue = result_queue

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def run_once(self) -> bool:
        """
        Process exactly one pending task.

        Reads the queue → selects a worker → assigns the task → executes
        via the worker → pushes the result to the ResultQueue.

        Returns
        -------
        bool
            ``True`` if a task was dispatched, ``False`` if the queue was
            empty or no capable agent could be found.
        """
        task = self._task_queue.get_next_task()
        if task is None:
            return False

        agent = self._scheduler.dispatch(task)
        if agent is None:
            self._task_queue.fail_task(task.id, "No capable agent available.")
            return False

        self._assign(task, agent)
        result = self._execute(task, agent)
        self._result_queue.push(result)

        if result.success:
            self._task_queue.complete_task(task.id, str(result.output))
        else:
            self._task_queue.fail_task(task.id, result.error or "Unknown error.")

        return True

    def collect_results(self) -> List[AgentResult]:
        """
        Drain and return all results currently in the ResultQueue.

        This is the Supervisor's *monitoring* step — it reads completed
        work without touching the TaskQueue.
        """
        return self._result_queue.drain()

    def run_until_empty(self) -> List[AgentResult]:
        """
        Convenience method: process every pending task, then collect results.

        Returns
        -------
        List[AgentResult]
            All results produced during this run.
        """
        while self.run_once():
            pass
        return self.collect_results()

    # ------------------------------------------------------------------ #
    #  Private helpers
    # ------------------------------------------------------------------ #

    def _assign(self, task: Task, agent: BaseAgent) -> None:
        """Record which agent owns this task."""
        task.assigned_agent = type(agent).__name__
        task.updated_at = datetime.now(timezone.utc)

    def _execute(self, task: Task, agent: BaseAgent) -> AgentResult:
        """
        Delegate execution to the chosen agent.

        Wraps the call in a try/except so any unhandled exception is
        captured as a failed ``AgentResult`` rather than crashing the loop.
        """
        try:
            output = agent.execute(task)
            return AgentResult(
                task_id=task.id,
                agent_name=type(agent).__name__,
                output=output,
                success=True,
            )
        except Exception as exc:  # noqa: BLE001
            return AgentResult(
                task_id=task.id,
                agent_name=type(agent).__name__,
                output=None,
                success=False,
                error=str(exc),
            )
