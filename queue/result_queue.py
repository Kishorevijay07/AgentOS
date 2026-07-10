from __future__ import annotations

from collections import deque
from threading import Lock
from typing import Deque, List, Optional

from models.result import AgentResult


class ResultQueue:
    """
    Thread-safe queue that decouples workers from the Supervisor.

    Workers never hand results back directly.  Instead each worker pushes an
    ``AgentResult`` here, and the Supervisor drains this queue during its
    monitoring pass.  This indirection makes the architecture horizontally
    scalable — workers and the Supervisor only share this queue, nothing else.
    """

    def __init__(self) -> None:
        self._results: Deque[AgentResult] = deque()
        self._lock = Lock()

    # ------------------------------------------------------------------ #
    #  Write
    # ------------------------------------------------------------------ #

    def push(self, result: AgentResult) -> None:
        """Enqueue a worker result."""
        with self._lock:
            self._results.append(result)

    # ------------------------------------------------------------------ #
    #  Read
    # ------------------------------------------------------------------ #

    def pop(self) -> Optional[AgentResult]:
        """
        Dequeue and return the oldest result.

        Returns ``None`` if the queue is empty.
        """
        with self._lock:
            return self._results.popleft() if self._results else None

    def drain(self) -> List[AgentResult]:
        """
        Return **all** pending results and clear the queue in one operation.

        Prefer this over repeated :py:meth:`pop` calls when the Supervisor
        wants to process an entire batch at once.
        """
        with self._lock:
            results = list(self._results)
            self._results.clear()
            return results

    def is_empty(self) -> bool:
        """Return ``True`` when no results are waiting."""
        with self._lock:
            return len(self._results) == 0

    def __len__(self) -> int:
        """Return the number of results currently queued."""
        with self._lock:
            return len(self._results)

    def __repr__(self) -> str:
        with self._lock:
            return f"ResultQueue(pending={len(self._results)})"
