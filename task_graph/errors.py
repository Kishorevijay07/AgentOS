from __future__ import annotations

from typing import List
from uuid import UUID


class GraphError(Exception):
    """Base class for every error raised by the Task Graph engine."""


class DuplicateTaskError(GraphError):
    """A task with the same id is already present in the graph."""


class UnknownTaskError(GraphError):
    """An operation referenced a task id that is not in the graph."""


class CycleDetectedError(GraphError):
    """
    An edge would make (or the graph already contains) a dependency cycle.

    Carries the offending cycle as an ordered list of task ids so callers and
    logs can show exactly which chain is circular.
    """

    def __init__(self, cycle: List[UUID]) -> None:
        self.cycle = cycle
        pretty = " -> ".join(str(t)[:8] for t in cycle)
        super().__init__(f"Dependency cycle detected: {pretty}")


class InvalidTransitionError(GraphError):
    """A node was asked to make a lifecycle transition that is not permitted."""
