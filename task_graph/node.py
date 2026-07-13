from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Set
from uuid import UUID

from pydantic import BaseModel, Field

from models.enums import Priority
from models.task import Task
from task_graph.state import NodeState


class ExecutionAttempt(BaseModel):
    """
    One entry in a node's execution history — a single attempt to run the task.

    Retry, reflection, and checkpointing all need to know *what happened each
    time* a task ran, not just the final outcome. ``execution_id`` links this
    attempt to the corresponding :class:`~result_store.ExecutionRecord`, so the
    graph's history and the result store's trace can be cross-referenced.
    """

    attempt: int = Field(ge=1, description="1-based attempt number.")
    worker_id: Optional[str] = Field(default=None, description="Worker that ran it.")
    started_at: datetime
    finished_at: Optional[datetime] = None
    success: Optional[bool] = None
    error: Optional[str] = None
    execution_id: Optional[UUID] = None


class TaskNode(BaseModel):
    """
    A node in the task DAG: the executable :class:`Task` plus the graph state
    that surrounds it.

    Design note — *wrap, don't copy*
    --------------------------------
    The node embeds the original :class:`Task` rather than duplicating its
    fields, so ``task_id``, ``description``, ``priority``, and ``dependencies``
    have a single source of truth. The node adds only what the *graph* owns:
    the lifecycle ``state``, the reverse edges (``children``), the
    ``assigned_worker``, per-attempt ``history``, and free-form ``metadata``.

    The convenience properties below expose the fields your spec lists as
    "node fields" without denormalising them.
    """

    task: Task
    state: NodeState = NodeState.BLOCKED
    children: Set[UUID] = Field(default_factory=set)
    history: List[ExecutionAttempt] = Field(default_factory=list)
    assigned_worker: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # ------------------------------------------------------------------ #
    #  Read-through properties (single source of truth = the wrapped Task)
    # ------------------------------------------------------------------ #

    @property
    def task_id(self) -> UUID:
        return self.task.id

    @property
    def description(self) -> str:
        return self.task.description

    @property
    def priority(self) -> Priority | str:
        return self.task.priority

    @property
    def dependencies(self) -> List[UUID]:
        """Task ids this node depends on (its parents)."""
        return self.task.dependencies

    @property
    def required_capabilities(self) -> List[str]:
        return self.task.required_capabilities

    @property
    def retry_count(self) -> int:
        return self.task.retry_count

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"TaskNode(id={str(self.task_id)[:8]}, state={self.state.value}, "
            f"deps={len(self.dependencies)}, children={len(self.children)})"
        )
