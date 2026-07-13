from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from datetime import datetime, timezone
from threading import Lock
from typing import TYPE_CHECKING, Deque, List, Optional, Set
from uuid import UUID

from models.enums import Status
from models.task import Task

if TYPE_CHECKING:
    from agents.base import BaseAgent


# Priority order for sorting (lower number = higher priority)
_PRIORITY_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}


class AbstractTaskQueue(ABC):
    """
    Contract that every Task Queue backend must satisfy.

    The Supervisor and Kernel depend on *this* type, never on
    :class:`TaskQueue` directly.  A future ``RedisTaskQueue`` (backed by a
    Redis list or stream for distributed, cross-process dispatch) can slot in
    at the construction site without changing any consumer — the signatures
    below are deliberately backend-agnostic.
    """

    @abstractmethod
    def add_task(self, task: Task) -> None:
        """Add a new task to the pending queue (priority-ordered)."""

    @abstractmethod
    def get_next_task(self) -> Optional[Task]:
        """Pop the highest-priority pending task and mark it IN_PROGRESS."""

    @abstractmethod
    def get_next_for_agent(self, agent: "BaseAgent") -> Optional[Task]:
        """Pop the best pending task whose caps + dependencies the agent satisfies."""

    @abstractmethod
    def complete_task(self, task_id: UUID, result: str = "") -> bool:
        """Mark an in-progress task as COMPLETED."""

    @abstractmethod
    def fail_task(self, task_id: UUID, reason: str = "") -> bool:
        """Mark an in-progress task as FAILED."""

    @abstractmethod
    def retry_task(self, task_id: UUID) -> bool:
        """Re-queue a FAILED task and increment its ``retry_count``."""

    @abstractmethod
    def cancel_task(self, task_id: UUID) -> bool:
        """Cancel a PENDING or IN_PROGRESS task."""

    @abstractmethod
    def pending_tasks(self) -> List[Task]:
        """Return a snapshot of all tasks still waiting in the queue."""

    @abstractmethod
    def is_empty(self) -> bool:
        """``True`` when no tasks are pending or in-progress."""


class TaskQueue(AbstractTaskQueue):
    """
    In-memory Priority Task Queue — v2.

    Interface is intentionally backend-agnostic; the same method signatures
    will be preserved when swapping to Redis or another backend later.

    v2 additions
    ------------
    * ``get_next_for_agent(agent)``  — capability + dependency aware dispatch
    * ``retry_task(task_id)``        — re-queue a failed task, bump retry_count
    * ``cancel_task(task_id)``       — cancel pending or in-progress tasks
    * ``overdue_tasks()``            — surface tasks past their deadline
    """

    def __init__(self) -> None:
        self._pending: Deque[Task] = deque()
        self._in_progress: dict[UUID, Task] = {}
        self._completed: List[Task] = []
        self._failed: List[Task] = []
        self._cancelled: List[Task] = []
        self._lock = Lock()   # thread-safe for concurrent agents

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    def _resort_pending(self) -> None:
        """Re-sort the pending deque by priority (must be called under lock)."""
        self._pending = deque(
            sorted(
                self._pending,
                key=lambda t: _PRIORITY_ORDER.get(
                    t.priority if isinstance(t.priority, str) else t.priority.value,
                    99,
                ),
            )
        )

    def _completed_ids(self) -> Set[UUID]:
        """Return the set of UUIDs for all completed tasks (must be called under lock)."""
        return {t.id for t in self._completed}

    def _dependencies_satisfied(self, task: Task) -> bool:
        """
        True when every dependency UUID is in the completed set.
        Must be called under lock.
        """
        if not task.dependencies:
            return True
        done = self._completed_ids()
        return all(dep_id in done for dep_id in task.dependencies)

    # ------------------------------------------------------------------ #
    #  Write Operations
    # ------------------------------------------------------------------ #

    def add_task(self, task: Task) -> None:
        """Add a new task to the pending queue (priority-sorted)."""
        with self._lock:
            self._pending.append(task)
            self._resort_pending()

    def get_next_task(self) -> Optional[Task]:
        """
        Pop the highest-priority pending task and mark it IN_PROGRESS.

        Returns ``None`` if the queue is empty.

        .. note::
            This method does **not** check ``required_capabilities`` or
            dependency satisfaction.  Prefer :meth:`get_next_for_agent` when
            dispatching to a specific agent.
        """
        with self._lock:
            if not self._pending:
                return None

            task = self._pending.popleft()
            task.status = Status.IN_PROGRESS
            task.updated_at = datetime.now(timezone.utc)
            self._in_progress[task.id] = task
            return task

    def get_next_for_agent(self, agent: "BaseAgent") -> Optional[Task]:
        """
        Pop and return the highest-priority pending task that:

        1. Has all ``required_capabilities`` covered by ``agent.capabilities``.
        2. Has all ``dependencies`` in the completed set.

        Marks the task ``IN_PROGRESS`` before returning.
        Returns ``None`` when no eligible task is found.
        """
        agent_caps = set(agent.capabilities)
        with self._lock:
            done = self._completed_ids()
            for task in list(self._pending):  # iterate priority-sorted snapshot
                required = set(task.required_capabilities)
                deps_ok = all(dep_id in done for dep_id in task.dependencies)
                caps_ok = required.issubset(agent_caps)
                if deps_ok and caps_ok:
                    self._pending.remove(task)
                    task.status = Status.IN_PROGRESS
                    task.updated_at = datetime.now(timezone.utc)
                    self._in_progress[task.id] = task
                    return task
        return None

    def complete_task(self, task_id: UUID, result: str = "") -> bool:
        """
        Mark an in-progress task as COMPLETED.

        Returns ``True`` if successful, ``False`` if ``task_id`` not found.
        """
        with self._lock:
            task = self._in_progress.pop(task_id, None)
            if task is None:
                return False

            task.status = Status.COMPLETED
            task.result = result
            task.updated_at = datetime.now(timezone.utc)
            self._completed.append(task)
            return True

    def fail_task(self, task_id: UUID, reason: str = "") -> bool:
        """
        Mark an in-progress task as FAILED.

        Returns ``True`` if successful, ``False`` if ``task_id`` not found.
        """
        with self._lock:
            task = self._in_progress.pop(task_id, None)
            if task is None:
                return False

            task.status = Status.FAILED
            task.result = reason
            task.updated_at = datetime.now(timezone.utc)
            self._failed.append(task)
            return True

    def retry_task(self, task_id: UUID) -> bool:
        """
        Move a FAILED task back to the pending queue and increment its
        ``retry_count``.

        Returns ``True`` if successful, ``False`` if the task is not in the
        failed list.
        """
        with self._lock:
            for i, task in enumerate(self._failed):
                if task.id == task_id:
                    self._failed.pop(i)
                    task.status = Status.PENDING
                    task.retry_count += 1
                    task.updated_at = datetime.now(timezone.utc)
                    self._pending.append(task)
                    self._resort_pending()
                    return True
        return False

    def cancel_task(self, task_id: UUID) -> bool:
        """
        Cancel a task that is either PENDING or IN_PROGRESS.

        Returns ``True`` if the task was found and cancelled, ``False``
        otherwise.
        """
        with self._lock:
            # Search in-progress first.
            task = self._in_progress.pop(task_id, None)
            if task is not None:
                task.status = Status.CANCELLED
                task.updated_at = datetime.now(timezone.utc)
                self._cancelled.append(task)
                return True

            # Search pending deque.
            for i, task in enumerate(self._pending):
                if task.id == task_id:
                    self._pending.remove(task)
                    task.status = Status.CANCELLED
                    task.updated_at = datetime.now(timezone.utc)
                    self._cancelled.append(task)
                    return True

        return False

    # ------------------------------------------------------------------ #
    #  Read Operations
    # ------------------------------------------------------------------ #

    def pending_tasks(self) -> List[Task]:
        """Return a snapshot of all tasks still waiting in the queue."""
        with self._lock:
            return list(self._pending)

    def in_progress_tasks(self) -> List[Task]:
        """Return all tasks currently being executed."""
        with self._lock:
            return list(self._in_progress.values())

    def completed_tasks(self) -> List[Task]:
        """Return all successfully completed tasks."""
        with self._lock:
            return list(self._completed)

    def failed_tasks(self) -> List[Task]:
        """Return all failed tasks."""
        with self._lock:
            return list(self._failed)

    def cancelled_tasks(self) -> List[Task]:
        """Return all cancelled tasks."""
        with self._lock:
            return list(self._cancelled)

    def overdue_tasks(self) -> List[Task]:
        """
        Return all PENDING or IN_PROGRESS tasks whose ``deadline`` has passed.

        Tasks without a deadline are never considered overdue.
        """
        now = datetime.now(timezone.utc)
        with self._lock:
            overdue: List[Task] = []
            for task in list(self._pending) + list(self._in_progress.values()):
                if task.deadline is not None and task.deadline < now:
                    overdue.append(task)
        return overdue

    def is_empty(self) -> bool:
        """``True`` when no tasks are pending or in-progress."""
        with self._lock:
            return len(self._pending) == 0 and len(self._in_progress) == 0

    def __len__(self) -> int:
        """Total tasks currently pending."""
        with self._lock:
            return len(self._pending)

    def __repr__(self) -> str:
        return (
            f"TaskQueue("
            f"pending={len(self._pending)}, "
            f"in_progress={len(self._in_progress)}, "
            f"completed={len(self._completed)}, "
            f"failed={len(self._failed)}, "
            f"cancelled={len(self._cancelled)})"
        )
