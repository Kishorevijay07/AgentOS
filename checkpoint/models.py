from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from pydantic import BaseModel, Field

from task_graph.node import TaskNode


class Checkpoint(BaseModel):
    """
    A serializable snapshot of a run's **execution state** — enough to resume it
    after a crash exactly where it left off.

    Why the graph is the whole story
    --------------------------------
    The task graph is the single source of truth for what has happened: every
    :class:`~task_graph.node.TaskNode` carries its lifecycle ``state``, its
    dependencies/children, and — via ``node.history`` — the per-attempt execution
    record (worker, timing, success/error, ``execution_id``). Because ``TaskNode``
    is a Pydantic model, snapshotting the node list is lossless *and*
    JSON-serialisable for free. Add the reflection budget (so a resumed run
    can't exceed its replan cap) and the tick counter, and the run is fully
    reconstructable.

    A checkpoint is pure data: producing and applying one lives on the Kernel.
    """

    version: str = "0.9"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    tick_count: int = 0
    replans_done: int = 0
    nodes: List[TaskNode] = Field(default_factory=list)

    def summary(self) -> dict:
        """A small human-readable digest (states histogram) for logs/monitoring."""
        from collections import Counter

        states = Counter(n.state.value for n in self.nodes)
        return {
            "version": self.version,
            "created_at": self.created_at.isoformat(),
            "tick_count": self.tick_count,
            "replans_done": self.replans_done,
            "nodes": len(self.nodes),
            "states": dict(states),
        }
