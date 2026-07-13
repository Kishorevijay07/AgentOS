from __future__ import annotations

import threading
from collections import Counter
from typing import Dict, List, Optional

from agents.base import BaseAgent
from config.settings import KernelSettings
from events.event import Event
from events.event_type import EventType
from kernel.context import KernelContext
from kernel.dispatcher import Dispatcher
from kernel.lifecycle import KernelState, Lifecycle
from kernel.tick import Tick, TickResult
from models.result import AgentResult
from models.task import Task


class Kernel:
    """
    The AgentOS Kernel — the runtime heartbeat.

    Like an OS kernel it is deliberately unintelligent: it **coordinates** and
    nothing more. It does not know how to research or write code — workers do
    that. The Kernel only:

    * owns the object graph (via a single :class:`KernelContext`),
    * runs the lifecycle (BOOTING → RUNNING → PAUSED → STOPPING → STOPPED),
    * turns the loop as discrete **ticks** (assign → collect → heartbeat),
    * and monitors health.

    Two ways to drive it
    --------------------
    * **Manual / deterministic** — call :meth:`tick` to advance exactly one
      iteration (ideal for tests and simulation), or :meth:`run_until_idle` to
      tick until the queue drains.
    * **Threaded** — :meth:`run` starts a background loop that ticks every
      ``settings.tick_interval_seconds``; :meth:`pause`, :meth:`resume`, and
      :meth:`stop` steer it.

    Dependency injection & swappability are handled entirely by the
    :class:`KernelContext` (see ADR-0008): construct one with an override to
    point a subsystem at Redis/Kafka; the Kernel and every module below it are
    untouched.
    """

    def __init__(
        self,
        context: Optional[KernelContext] = None,
        *,
        settings: Optional[KernelSettings] = None,
    ) -> None:
        self._ctx = context or KernelContext.in_memory(settings)
        self._dispatcher = Dispatcher(self._ctx)
        self._tick = Tick(self._ctx, self._dispatcher)
        self._lifecycle = Lifecycle()

        # agent_id → live agent, so the Kernel can drive lifecycle on shutdown.
        self._agents: Dict[str, BaseAgent] = {}

        # Threaded-loop machinery.
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._tick_count = 0
        self._tick_lock = threading.Lock()

        self._ctx.logger.info("Kernel constructed (state=%s).", self.state.value)

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    def boot(self) -> "Kernel":
        """Complete boot: BOOTING → RUNNING. Returns ``self`` for fluent setup."""
        if self._lifecycle.is_(KernelState.BOOTING):
            self._lifecycle.transition(KernelState.RUNNING)
        self._ctx.logger.info(
            "Kernel booted → RUNNING (%d agent(s)).", len(self._agents)
        )
        return self

    def register_agent(self, agent: BaseAgent) -> str:
        """
        Admit *agent* to the worker pool and return its ``agent_id``.

        Registers it, wires its registry/bus integration, and initialises it
        (``INITIALIZING → IDLE`` + ``AGENT_ONLINE``).
        """
        agent_id = self._ctx.registry.register(agent)
        configure = getattr(agent, "_configure_worker", None)
        if callable(configure):
            configure(
                registry=self._ctx.registry, bus=self._ctx.event_bus, agent_id=agent_id
            )
        agent.initialize()
        self._agents[agent_id] = agent
        self._ctx.logger.info(
            "Registered agent %s (caps=%s).", agent_id, list(agent.capabilities)
        )
        return agent_id

    def run(self) -> "Kernel":
        """
        Start the threaded tick loop (BOOTING → RUNNING if needed).

        Idempotent: calling ``run`` on an already-running kernel is a no-op.
        """
        if self._lifecycle.is_(KernelState.BOOTING):
            self._lifecycle.transition(KernelState.RUNNING)
        if not self._lifecycle.is_(KernelState.RUNNING):
            raise RuntimeError(f"Cannot run() from state {self.state.value}.")
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="agentos-kernel", daemon=True
        )
        self._thread.start()
        self._ctx.logger.info("Kernel run loop started.")
        return self

    def pause(self) -> None:
        """Suspend ticking without stopping the loop: RUNNING → PAUSED."""
        self._lifecycle.transition(KernelState.PAUSED)
        self._ctx.logger.info("Kernel paused.")

    def resume(self) -> None:
        """Resume ticking: PAUSED → RUNNING."""
        self._lifecycle.transition(KernelState.RUNNING)
        self._ctx.logger.info("Kernel resumed.")

    def stop(self) -> None:
        """
        Stop the run loop and drive the lifecycle to STOPPED.

        Idempotent and safe whether or not the threaded loop is running.
        """
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        self._thread = None
        if self._lifecycle.is_(
            KernelState.RUNNING, KernelState.PAUSED, KernelState.BOOTING
        ):
            self._lifecycle.transition(KernelState.STOPPING)
        if self._lifecycle.is_(KernelState.STOPPING):
            self._lifecycle.transition(KernelState.STOPPED)

    def shutdown(self) -> None:
        """
        Full teardown: stop the loop, then shut every worker down
        (``AGENT_OFFLINE`` per agent). Leaves the kernel STOPPED.
        """
        self.stop()
        for agent_id, agent in list(self._agents.items()):
            try:
                agent.shutdown()
            except Exception:  # noqa: BLE001
                self._ctx.logger.exception("Agent %s raised during shutdown.", agent_id)
        self._ctx.logger.info("Kernel shutdown complete.")

    # ------------------------------------------------------------------ #
    #  Work submission + execution
    # ------------------------------------------------------------------ #

    def submit(self, task: Task) -> None:
        """Enqueue *task* and publish ``TASK_CREATED``."""
        self._ctx.task_queue.add_task(task)
        self._ctx.event_bus.publish(
            Event(
                type=EventType.TASK_CREATED,
                payload={
                    "task_id": str(task.id),
                    "description": task.description,
                    "required_capabilities": list(task.required_capabilities),
                },
                source="Kernel",
            )
        )

    def tick(self) -> TickResult:
        """Advance the runtime exactly one iteration and return its result."""
        if self._lifecycle.is_(KernelState.STOPPED):
            raise RuntimeError("Cannot tick() a STOPPED kernel.")
        with self._tick_lock:
            self._tick_count += 1
            n = self._tick_count
        return self._tick.run_once(n)

    def run_until_idle(self) -> List[AgentResult]:
        """
        Tick until the task queue is fully drained, returning all results.

        Terminates even if a wave makes no progress (e.g. tasks with no capable
        worker), so it never spins.
        """
        results: List[AgentResult] = []
        while not self._ctx.task_queue.is_empty():
            before = len(self._ctx.task_queue.pending_tasks())
            result = self.tick()
            results.extend(result.results)
            # No-progress guard: nothing dispatched and pending didn't shrink.
            if result.dispatched == 0 and len(self._ctx.task_queue.pending_tasks()) >= before:
                break
        return results

    #: Backwards-compatible alias for :meth:`run_until_idle`.
    run_until_empty = run_until_idle

    # ------------------------------------------------------------------ #
    #  Threaded loop
    # ------------------------------------------------------------------ #

    def _loop(self) -> None:
        interval = self._ctx.settings.tick_interval_seconds
        while not self._stop_event.is_set():
            if self._lifecycle.is_(KernelState.RUNNING):
                self.tick()
            # Responsive sleep — wakes immediately when stop() sets the event.
            self._stop_event.wait(interval)

    # ------------------------------------------------------------------ #
    #  Health / inspection
    # ------------------------------------------------------------------ #

    def health(self) -> dict:
        """Return a snapshot of runtime health for monitoring."""
        agents = self._ctx.registry.list_agents()
        by_status = Counter(r.status.value for r in agents)
        return {
            "state": self.state.value,
            "tick_count": self._tick_count,
            "pending": len(self._ctx.task_queue.pending_tasks()),
            "workers": {
                "total": len(agents),
                "idle": by_status.get("idle", 0),
                "busy": by_status.get("busy", 0),
                "offline": by_status.get("offline", 0),
            },
        }

    @property
    def state(self) -> KernelState:
        return self._lifecycle.state

    @property
    def tick_count(self) -> int:
        return self._tick_count

    @property
    def context(self) -> KernelContext:
        return self._ctx

    @property
    def settings(self) -> KernelSettings:
        return self._ctx.settings

    @property
    def bus(self):
        return self._ctx.event_bus

    @property
    def registry(self):
        return self._ctx.registry

    @property
    def store(self):
        return self._ctx.result_store

    @property
    def task_queue(self):
        return self._ctx.task_queue

    def __repr__(self) -> str:
        return (
            f"Kernel(state={self.state.value}, agents={len(self._agents)}, "
            f"ticks={self._tick_count})"
        )


def build_kernel(settings: Optional[KernelSettings] = None) -> Kernel:
    """
    Factory returning a fully wired, in-memory :class:`Kernel`.

    For a distributed backend, build a :class:`KernelContext` with the relevant
    abstraction override and pass it to :class:`Kernel` directly.
    """
    return Kernel(KernelContext.in_memory(settings))
