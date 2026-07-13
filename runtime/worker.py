from __future__ import annotations

from datetime import datetime
from typing import Any, List, Protocol, runtime_checkable

from models.task import Task


@runtime_checkable
class Worker(Protocol):
    """
    The structural contract every managed worker must satisfy.

    Defined as a :class:`typing.Protocol` (structural typing) so the runtime
    depends on a *shape*, not on the ``agents`` package — any object exposing
    these members is a worker. The existing :class:`~agents.base.BaseAgent`
    satisfies it as-is, so all concrete agents are runtime workers with zero
    changes, and third-party / plugin workers need no shared base class.

    A worker is a *dumb executor*: it knows how to do its own work and manage
    its own resources, but it does **not** decide when to run, retry, or shut
    down — the runtime does.
    """

    #: Capability tags this worker can satisfy (e.g. ``["code", "research"]``).
    capabilities: List[str]

    def initialize(self) -> None:
        """One-time setup: load models, open connections, warm caches."""
        ...

    def execute(self, task: Task) -> Any:
        """Execute *task* and return its result (or raise on failure)."""
        ...

    def heartbeat(self) -> datetime:
        """Return a liveness timestamp; may raise if the worker is unhealthy."""
        ...

    def pause(self) -> None:
        """Suspend the worker without tearing it down."""
        ...

    def resume(self) -> None:
        """Resume a paused worker."""
        ...

    def shutdown(self) -> None:
        """Tear the worker down permanently and release its resources."""
        ...
