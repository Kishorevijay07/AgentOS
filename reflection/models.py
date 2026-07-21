from __future__ import annotations

from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from models.enums import Priority


class ReflectionVerdict(str, Enum):
    """The reflector's judgement of a completed task's output."""

    ACCEPT = "accept"   # output is good enough; do nothing
    REPLAN = "replan"   # output needs follow-up / correction; inject new work


class ReflectionRequest(BaseModel):
    """
    Everything a :class:`~reflection.reflector.Reflector` needs to judge one
    completed task — and nothing more.

    Deliberately graph-free: a reflector receives this value object and returns a
    :class:`ReflectionDecision`. It never touches the task graph, the scheduler,
    or a worker (the :class:`~reflection.coordinator.ReflectionCoordinator` owns
    every side effect). That keeps reflectors pure and trivially testable.
    """

    task_id: UUID
    description: str
    output: str = ""
    success: bool = True
    error: Optional[str] = None
    attempt: int = 0
    goal: Optional[str] = None
    allowed_capabilities: List[str] = Field(default_factory=list)


class ProposedTask(BaseModel):
    """A follow-up task a reflector wants injected into the live graph."""

    description: str = Field(min_length=1)
    capabilities: List[str] = Field(default_factory=list)
    priority: Priority = Priority.MEDIUM
    depends_on_parent: bool = Field(
        default=True,
        description="If True, the new task runs after the reflected task.",
    )


class ReflectionDecision(BaseModel):
    """
    The reflector's structured verdict.

    ``ACCEPT`` ends the loop for this task; ``REPLAN`` carries the follow-up
    tasks the coordinator will add to the graph. A decision is pure data — the
    coordinator, not the reflector, decides whether the replan budget allows it.
    """

    verdict: ReflectionVerdict = ReflectionVerdict.ACCEPT
    reason: str = ""
    new_tasks: List[ProposedTask] = Field(default_factory=list)

    @classmethod
    def accept(cls, reason: str = "") -> "ReflectionDecision":
        """Convenience constructor for the (common) accept case."""
        return cls(verdict=ReflectionVerdict.ACCEPT, reason=reason)
