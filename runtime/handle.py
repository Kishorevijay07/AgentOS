from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from runtime.errors import InvalidWorkerStateError
from runtime.lifecycle import WorkerState, can_transition
from runtime.metrics import WorkerMetrics
from runtime.worker import Worker


class WorkerHandle:
    """
    The runtime's record for a single managed worker.

    It bundles the worker with everything the runtime owns *about* it — the
    authoritative lifecycle ``state``, ``metrics``, the ``current_task``, the
    last heartbeat, and a per-worker :class:`threading.Lock`. Every mutation of a
    worker goes through its handle under that lock, which is how the runtime
    achieves **failure isolation**: one worker's state churn or crash can never
    corrupt another's.

    The handle is intentionally *not* a Pydantic model — it holds a live worker
    object and a lock, which are not serialisable. A serialisable snapshot for
    metrics/status is exposed via :attr:`metrics` and :meth:`status`.
    """

    def __init__(self, worker_id: str, worker: Worker) -> None:
        self.worker_id: str = worker_id
        self.worker: Worker = worker
        self.capabilities: List[str] = list(worker.capabilities)
        self.state: WorkerState = WorkerState.INITIALIZING
        self.current_task: Optional[UUID] = None
        self.registered_at: datetime = datetime.now(timezone.utc)
        self.last_heartbeat: datetime = datetime.now(timezone.utc)
        self.metrics: WorkerMetrics = WorkerMetrics(worker_id=worker_id)
        self.lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------ #
    #  State management (caller holds self.lock)
    # ------------------------------------------------------------------ #

    def transition(self, target: WorkerState) -> None:
        """Move to *target* or raise :class:`InvalidWorkerStateError`."""
        if self.state == target:
            return
        if not can_transition(self.state, target):
            raise InvalidWorkerStateError(
                f"Illegal worker transition {self.state.value} → {target.value} "
                f"for worker {self.worker_id}."
            )
        self.state = target

    def touch_heartbeat(self, at: Optional[datetime] = None) -> None:
        self.last_heartbeat = at or datetime.now(timezone.utc)

    def status(self) -> dict:
        """Return a JSON-serialisable status snapshot for monitoring."""
        return {
            "worker_id": self.worker_id,
            "state": self.state.value,
            "capabilities": list(self.capabilities),
            "current_task": str(self.current_task) if self.current_task else None,
            "last_heartbeat": self.last_heartbeat.isoformat(),
            "tasks_executed": self.metrics.tasks_executed,
            "success_rate": round(self.metrics.success_rate, 4),
        }

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"WorkerHandle(id={self.worker_id!r}, state={self.state.value}, caps={self.capabilities})"
