from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel


class ExecutionOutcome(BaseModel):
    """
    The result of one attempt to execute a task on a worker.

    This is the *only* value that crosses back from the runtime to the
    scheduler. It is deliberately self-describing (success, error, timing,
    timeout flag, execution id) so the scheduler can reconcile the task graph
    and apply retry policy without ever inspecting a worker.
    """

    task_id: UUID
    worker_id: str
    success: bool
    output: Any = None
    error: Optional[str] = None
    duration_seconds: float = 0.0
    timed_out: bool = False
    execution_id: Optional[UUID] = None
