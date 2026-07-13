from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class WorkerMetrics(BaseModel):
    """
    Per-worker execution metrics maintained by the runtime.

    Metrics are a first-class product surface — schedulers, autoscalers, and
    dashboards consume them — so they live in a validated model rather than
    scattered log lines. The runtime updates them under the worker's lock, so
    reads are always internally consistent.
    """

    worker_id: str
    tasks_executed: int = 0
    tasks_succeeded: int = 0
    tasks_failed: int = 0
    tasks_timed_out: int = 0
    total_execution_seconds: float = 0.0
    last_error: Optional[str] = None
    last_active_at: Optional[datetime] = None

    @property
    def average_execution_seconds(self) -> float:
        """Mean wall-clock execution time across completed attempts."""
        return (
            self.total_execution_seconds / self.tasks_executed
            if self.tasks_executed
            else 0.0
        )

    @property
    def success_rate(self) -> float:
        """Fraction of executed tasks that succeeded (0.0–1.0)."""
        return self.tasks_succeeded / self.tasks_executed if self.tasks_executed else 0.0

    def record(
        self,
        *,
        success: bool,
        duration_seconds: float,
        timed_out: bool = False,
        error: Optional[str] = None,
        at: Optional[datetime] = None,
    ) -> None:
        """Fold one completed execution into the running totals."""
        self.tasks_executed += 1
        self.total_execution_seconds += duration_seconds
        self.last_active_at = at
        if success:
            self.tasks_succeeded += 1
        else:
            self.tasks_failed += 1
            self.last_error = error
        if timed_out:
            self.tasks_timed_out += 1
