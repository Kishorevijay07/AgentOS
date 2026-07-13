from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional
from uuid import UUID

from models.task import Task
from task_graph.errors import GraphError
from task_graph.graph import AbstractTaskGraph, _priority_key
from task_queue.task_queue import AbstractTaskQueue

if TYPE_CHECKING:
    from agents.base import BaseAgent


class GraphTaskQueue(AbstractTaskQueue):
    """
    Adapts an :class:`AbstractTaskGraph` to the runtime's :class:`AbstractTaskQueue`
    port — so the existing ``Dispatcher`` / ``Kernel`` execute a DAG unchanged.

    This adapter is the concrete proof of the boundary you asked for: the
    Dispatcher pulls work with :meth:`get_next_task`, which returns only nodes
    the graph reports as ``ready``. The Dispatcher never sees a dependency; when
    it later calls :meth:`complete_task`, the graph unlocks the dependents and
    they surface on the next pull. Dependency logic lives entirely in the graph.

    Note
    ----
    Like the in-memory ``TaskQueue``, ``get_next_task`` is capability-blind
    (the Scheduler matches capabilities to a worker afterwards); ``get_next_for_agent``
    is the capability-aware variant.
    """

    def __init__(self, graph: AbstractTaskGraph) -> None:
        self._graph = graph

    # --- ingestion -------------------------------------------------------

    def add_task(self, task: Task) -> None:
        """Insert a task into the underlying graph (edges from its dependencies)."""
        try:
            self._graph.add_task(task)
        except GraphError:
            raise

    # --- dispatch --------------------------------------------------------

    def get_next_task(self) -> Optional[Task]:
        """Return the highest-priority ready task, marking its node RUNNING."""
        ready = self._graph.ready_tasks()
        if not ready:
            return None
        node = ready[0]  # already priority-sorted by the graph
        self._graph.mark_running(node.task_id)
        return node.task

    def get_next_for_agent(self, agent: "BaseAgent") -> Optional[Task]:
        """Return the best ready task whose capabilities *agent* satisfies."""
        agent_caps = set(agent.capabilities)
        for node in sorted(self._graph.ready_tasks(), key=_priority_key):
            if set(node.required_capabilities).issubset(agent_caps):
                self._graph.mark_running(node.task_id, worker_id=None)
                return node.task
        return None

    # --- completion ------------------------------------------------------

    def complete_task(self, task_id: UUID, result: str = "") -> bool:
        try:
            self._graph.mark_completed(task_id)
            return True
        except GraphError:
            return False

    def fail_task(self, task_id: UUID, reason: str = "") -> bool:
        try:
            self._graph.mark_failed(task_id, reason)
            return True
        except GraphError:
            return False

    def retry_task(self, task_id: UUID) -> bool:
        try:
            self._graph.reset_for_retry(task_id)
            return True
        except GraphError:
            return False

    def cancel_task(self, task_id: UUID) -> bool:
        try:
            self._graph.cancel_task(task_id)
            return True
        except GraphError:
            return False

    # --- inspection ------------------------------------------------------

    def pending_tasks(self) -> List[Task]:
        return [n.task for n in self._graph.pending_tasks()]

    def is_empty(self) -> bool:
        """True when the graph has no BLOCKED/READY/RUNNING work left."""
        return not self._graph.has_active_work()
