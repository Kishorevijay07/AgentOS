from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from threading import Lock
from typing import Deque, List, Optional
from uuid import UUID

from models.enums import Priority, Status
from models.task import Task


# Priority order for sorting (lower number = higher priority)
_PRIORITY_ORDER = {
    Priority.CRITICAL: 0,
    Priority.HIGH: 1,
    Priority.MEDIUM: 2,
    Priority.LOW: 3,
}


class TaskQueue:
    """
    In-memory Task Queue.

    Interface is intentionally backend-agnostic — the same method signatures
    will be preserved when swapping to Redis or any other backend later.
    """

    def __init__(self) -> None:
        self._pending: Deque[Task] = deque()
        self._in_progress: dict[UUID, Task] = {}
        self._completed: List[Task] = []
        self._failed: List[Task] = []
        self._lock = Lock()   # thread-safe for concurrent agents

    # ------------------------------------------------------------------ #
    #  Write Operations
    # ------------------------------------------------------------------ #

    def add_task(self, task: Task) -> None:
        """Add a new task to the pending queue (priority-sorted)."""
        with self._lock:
            self._pending.append(task)
            # Re-sort by priority so CRITICAL tasks bubble up
            sorted_tasks = sorted(
                self._pending,
                key=lambda t: _PRIORITY_ORDER.get(t.priority, 99),
            )
            self._pending = deque(sorted_tasks)

    def get_next_task(self) -> Optional[Task]:
        """
        Pop the highest-priority pending task and mark it IN_PROGRESS.
        Returns None if the queue is empty.
        """
        with self._lock:
            if not self._pending:
                return None

            task = self._pending.popleft()
            task.status = Status.IN_PROGRESS
            task.updated_at = datetime.now(timezone.utc)
            self._in_progress[task.id] = task
            return task

    def complete_task(self, task_id: UUID, result: str = "") -> bool:
        """
        Mark an in-progress task as COMPLETED.
        Returns True if successful, False if task_id not found.
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
        Returns True if successful, False if task_id not found.
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

    # ------------------------------------------------------------------ #
    #  Read Operations
    # ------------------------------------------------------------------ #

    def pending_tasks(self) -> List[Task]:
        """Return a snapshot of all tasks still waiting in the queue."""
        with self._lock:
            return list(self._pending)

    def completed_tasks(self) -> List[Task]:
        """Return all successfully completed tasks."""
        with self._lock:
            return list(self._completed)

    def failed_tasks(self) -> List[Task]:
        """Return all failed tasks."""
        with self._lock:
            return list(self._failed)

    def is_empty(self) -> bool:
        """True when no tasks are pending or in-progress."""
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
            f"failed={len(self._failed)})"
        )
