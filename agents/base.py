from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, List

from models.task import Task


class BaseAgent(ABC):
    """
    Abstract base class for all worker agents.

    Every concrete agent must declare its ``capabilities`` — a list of
    lowercase strings that describe what kinds of tasks the agent can handle.
    The Scheduler uses these capabilities to route tasks without ever
    hard-coding task-name checks.

    Lifecycle
    ---------
    Agents follow an OS-process lifecycle.  The :class:`WorkerMixin`
    (``agents/worker.py``) provides default implementations of every
    lifecycle method; concrete agents should inherit both::

        class CodingAgent(WorkerMixin, BaseAgent): ...

    The abstract stubs below ensure that any class claiming to be a
    ``BaseAgent`` exposes the full lifecycle contract, even if it does
    not use ``WorkerMixin``.
    """

    # Subclasses must override this with their own capability list.
    capabilities: List[str] = []

    # ------------------------------------------------------------------ #
    #  Core execution (must override)                                     #
    # ------------------------------------------------------------------ #

    @abstractmethod
    def execute(self, task: Task) -> Any:
        """
        Execute a task and return the result.

        Parameters
        ----------
        task:
            The ``Task`` object assigned by the Scheduler.

        Returns
        -------
        Any
            An arbitrary result value; the Supervisor will store this in
            ``task.result`` after the call completes.
        """

    # ------------------------------------------------------------------ #
    #  Lifecycle hooks (abstract stubs — WorkerMixin provides defaults)   #
    # ------------------------------------------------------------------ #

    @abstractmethod
    def initialize(self) -> None:
        """
        One-time setup called before the first ``execute``.

        Use this to load models, open connections, warm up caches, etc.
        Must transition the worker from ``INITIALIZING`` → ``IDLE``.
        """

    @abstractmethod
    def pause(self) -> None:
        """
        Suspend work without terminating the worker.

        Called during rate-limit backoff, maintenance windows, or when the
        Supervisor needs to temporarily stop the worker.
        Transitions: ``BUSY`` or ``IDLE`` → ``PAUSED``.
        """

    @abstractmethod
    def resume(self) -> None:
        """
        Undo a previous ``pause``.

        Transitions: ``PAUSED`` → ``IDLE``.
        """

    @abstractmethod
    def shutdown(self) -> None:
        """
        Tear down the worker permanently.

        Close connections, flush buffers, deregister from the registry.
        Transitions: any state → ``SHUTTING_DOWN`` → ``TERMINATED``.
        """

    @abstractmethod
    def heartbeat(self) -> datetime:
        """
        Emit a keep-alive signal.

        Implementations should update ``last_heartbeat`` in the
        ``AgentRegistry`` and optionally publish an ``AGENT_HEARTBEAT``
        event on the bus.

        Returns
        -------
        datetime
            UTC timestamp of the heartbeat.
        """
