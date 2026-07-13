from __future__ import annotations

import threading
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from agents.registry import AgentRegistry
    from events.bus import AbstractEventBus


class WorkerState(str, Enum):
    """
    OS-process-style lifecycle states for an AgentOS worker.

    Transitions
    -----------
    INITIALIZING  ──► IDLE         (initialize() completes)
    IDLE          ──► BUSY         (task dispatched)
    BUSY          ──► IDLE         (task finished / failed)
    IDLE / BUSY   ──► PAUSED       (pause())
    PAUSED        ──► IDLE         (resume())
    any           ──► SHUTTING_DOWN (shutdown() called)
    SHUTTING_DOWN ──► TERMINATED   (teardown complete)
    """

    INITIALIZING = "initializing"
    IDLE = "idle"
    BUSY = "busy"
    PAUSED = "paused"
    SHUTTING_DOWN = "shutting_down"
    TERMINATED = "terminated"


# Valid state transitions: from → {allowed targets}
_TRANSITIONS: dict[WorkerState, set[WorkerState]] = {
    WorkerState.INITIALIZING: {WorkerState.IDLE, WorkerState.SHUTTING_DOWN},
    WorkerState.IDLE: {WorkerState.BUSY, WorkerState.PAUSED, WorkerState.SHUTTING_DOWN},
    WorkerState.BUSY: {WorkerState.IDLE, WorkerState.PAUSED, WorkerState.SHUTTING_DOWN},
    WorkerState.PAUSED: {WorkerState.IDLE, WorkerState.SHUTTING_DOWN},
    WorkerState.SHUTTING_DOWN: {WorkerState.TERMINATED},
    WorkerState.TERMINATED: set(),
}


class WorkerMixin:
    """
    Default implementations of the full agent lifecycle.

    Concrete agents should inherit ``WorkerMixin`` **before** ``BaseAgent``
    so Python's MRO picks up these defaults first::

        class CodingAgent(WorkerMixin, BaseAgent):
            capabilities = ["code", "implement"]

            def execute(self, task):
                ...

    Override any lifecycle method in the concrete class to add real logic
    (e.g. loading an LLM in ``initialize``).

    Thread safety
    -------------
    All state mutations acquire ``self._worker_lock``.  The lock is created
    lazily in ``_ensure_worker_state`` so the mixin does not require a custom
    ``__init__`` in simple cases.  If a concrete class defines ``__init__``,
    call ``self._ensure_worker_state()`` or call ``super().__init__()``.

    Optional integrations
    ----------------------
    Pass ``registry`` and / or ``bus`` to ``_configure_worker`` after
    instantiation so heartbeats update the registry and publish events::

        agent = CodingAgent()
        agent._configure_worker(registry=registry, bus=bus)
    """

    # Injected optionally; None means "no registry / no bus integration".
    _worker_registry: Optional[AgentRegistry] = None
    _worker_bus: Optional[AbstractEventBus] = None
    _worker_agent_id: Optional[str] = None  # set by registry.register()

    def _ensure_worker_state(self) -> None:
        """Lazily initialise mixin state — safe to call multiple times."""
        if not hasattr(self, "_worker_state"):
            self._worker_lock = threading.Lock()
            self._worker_state: WorkerState = WorkerState.INITIALIZING

    def _configure_worker(
        self,
        *,
        registry: Optional[AgentRegistry] = None,
        bus: Optional[AbstractEventBus] = None,
        agent_id: Optional[str] = None,
    ) -> None:
        """
        Wire up optional registry and event-bus integrations.

        Call this after construction (typically done by the bootstrap code
        that owns both the registry and the bus)::

            agent_id = registry.register(agent)
            agent._configure_worker(registry=registry, bus=bus, agent_id=agent_id)
        """
        self._ensure_worker_state()
        self._worker_registry = registry
        self._worker_bus = bus
        self._worker_agent_id = agent_id

    # ------------------------------------------------------------------ #
    #  State management                                                   #
    # ------------------------------------------------------------------ #

    @property
    def worker_state(self) -> WorkerState:
        """Current lifecycle state (thread-safe read)."""
        self._ensure_worker_state()
        with self._worker_lock:
            return self._worker_state

    def _transition(self, target: WorkerState) -> None:
        """
        Attempt a state transition.

        Raises
        ------
        RuntimeError
            If the transition is not permitted by the state machine.
        """
        self._ensure_worker_state()
        with self._worker_lock:
            allowed = _TRANSITIONS.get(self._worker_state, set())
            if target not in allowed:
                raise RuntimeError(
                    f"Invalid worker state transition: "
                    f"{self._worker_state} → {target}. "
                    f"Allowed: {allowed}"
                )
            self._worker_state = target

    # ------------------------------------------------------------------ #
    #  Lifecycle — default (no-op) implementations                       #
    # ------------------------------------------------------------------ #

    def initialize(self) -> None:
        """
        One-time setup.  Override to load models, open connections, etc.

        The default implementation performs no work beyond the state
        transition ``INITIALIZING → IDLE`` and publishing ``AGENT_ONLINE``.
        """
        self._ensure_worker_state()
        self._transition(WorkerState.IDLE)
        self._publish_agent_online()

    def pause(self) -> None:
        """
        Suspend the worker.  Override to flush in-flight work, etc.

        Transitions ``IDLE`` or ``BUSY`` → ``PAUSED``.
        """
        self._transition(WorkerState.PAUSED)

    def resume(self) -> None:
        """
        Resume from paused state.

        Transitions ``PAUSED`` → ``IDLE``.
        """
        self._transition(WorkerState.IDLE)

    def shutdown(self) -> None:
        """
        Permanently tear down the worker.

        Override to close connections, deregister from the registry, etc.
        Default: transitions to ``SHUTTING_DOWN`` then ``TERMINATED`` and
        publishes ``AGENT_OFFLINE``.
        """
        self._transition(WorkerState.SHUTTING_DOWN)
        # Override hook for subclass teardown (call super() at the end).
        self._transition(WorkerState.TERMINATED)
        self._publish_agent_offline()

    def heartbeat(self) -> datetime:
        """
        Emit a keep-alive signal.

        Updates ``last_heartbeat`` in the ``AgentRegistry`` (if configured)
        and publishes an ``AGENT_HEARTBEAT`` event (if a bus is configured).

        Returns
        -------
        datetime
            UTC timestamp of this heartbeat.
        """
        now = datetime.now(timezone.utc)

        # Ping the registry.
        if self._worker_registry is not None and self._worker_agent_id is not None:
            try:
                self._worker_registry.heartbeat(self._worker_agent_id)
            except KeyError:
                pass  # Agent was deregistered — heartbeat is a no-op.

        # Publish to the bus.
        if self._worker_bus is not None and self._worker_agent_id is not None:
            from events.event import Event
            from events.event_type import EventType

            self._worker_bus.publish(
                Event(
                    type=EventType.AGENT_HEARTBEAT,
                    payload={
                        "agent_id": self._worker_agent_id,
                        "state": self.worker_state.value,
                        "timestamp": now.isoformat(),
                    },
                    source=self._worker_agent_id,
                )
            )

        return now

    # ------------------------------------------------------------------ #
    #  Private event helpers                                              #
    # ------------------------------------------------------------------ #

    def _publish_agent_online(self) -> None:
        if self._worker_bus is None or self._worker_agent_id is None:
            return
        from events.event import Event
        from events.event_type import EventType

        caps = getattr(self, "capabilities", [])
        self._worker_bus.publish(
            Event(
                type=EventType.AGENT_ONLINE,
                payload={
                    "agent_id": self._worker_agent_id,
                    "capabilities": list(caps),
                },
                source=self._worker_agent_id,
            )
        )

    def _publish_agent_offline(self) -> None:
        if self._worker_bus is None or self._worker_agent_id is None:
            return
        from events.event import Event
        from events.event_type import EventType

        self._worker_bus.publish(
            Event(
                type=EventType.AGENT_OFFLINE,
                payload={"agent_id": self._worker_agent_id},
                source=self._worker_agent_id,
            )
        )
