from __future__ import annotations

import threading
from collections import Counter
from typing import List, Optional

from config.settings import KernelSettings
from events.event import Event
from events.event_type import EventType
from kernel.context import KernelContext
from kernel.lifecycle import KernelState, Lifecycle
from kernel.tick import Tick, TickResult
from models.task import Task
from runtime.outcome import ExecutionOutcome
from runtime.worker import Worker
from task_graph.adapter import GraphTaskQueue


class Kernel:
    """
    The AgentOS Kernel — the runtime heartbeat.

    Like an OS kernel it is deliberately unintelligent: it **coordinates** and
    nothing more. Since v0.7 it drives the unified graph runtime (ADR-0011):
    work lives in the task-graph DAG, workers live in the worker runtime, and
    one :class:`~scheduling.scheduler.ExecutionScheduler` connects them through
    a pluggable dispatch backend. The Kernel only:

    * owns the object graph (via a single :class:`KernelContext`),
    * runs the lifecycle (BOOTING → RUNNING → PAUSED → STOPPING → STOPPED),
    * turns the loop as discrete **ticks** (assign → collect → health),
    * and monitors health.

    Two ways to drive it
    --------------------
    * **Manual / deterministic** — call :meth:`tick` to advance exactly one
      iteration (ideal for tests and simulation), or :meth:`run_until_idle` to
      tick until the graph drains.
    * **Threaded** — :meth:`run` starts a background loop that ticks every
      ``settings.tick_interval_seconds``; :meth:`pause`, :meth:`resume`, and
      :meth:`stop` steer it.

    Dependency injection & swappability are handled entirely by the
    :class:`KernelContext` (ADR-0008): construct one with an override to point a
    subsystem at a distributed backend; the Kernel is untouched.
    """

    def __init__(
        self,
        context: Optional[KernelContext] = None,
        *,
        settings: Optional[KernelSettings] = None,
    ) -> None:
        self._ctx = context or KernelContext.in_memory(settings)
        self._tick = Tick(self._ctx)
        self._lifecycle = Lifecycle()

        # Threaded-loop machinery.
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._tick_count = 0
        self._tick_lock = threading.Lock()

        # Lazy adapter so callers (e.g. the PlanningService) can seed the graph
        # through the AbstractTaskQueue port they already speak.
        self._task_queue_adapter: Optional[GraphTaskQueue] = None

        self._ctx.logger.info("Kernel constructed (state=%s).", self.state.value)

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    def boot(self) -> "Kernel":
        """Complete boot: BOOTING → RUNNING. Returns ``self`` for fluent setup."""
        if self._lifecycle.is_(KernelState.BOOTING):
            self._ctx.scheduler.start()
            self._lifecycle.transition(KernelState.RUNNING)
        self._ctx.logger.info("Kernel booted → RUNNING.")
        return self

    def register_agent(self, agent: Worker) -> str:
        """
        Admit *agent* to the worker pool and return its ``agent_id``.

        Delegates to the worker runtime, which initialises the worker, isolates
        init failures, and publishes ``AGENT_ONLINE``.
        """
        agent_id = self._ctx.worker_runtime.register_worker(agent)
        self._ctx.logger.info("Registered agent %s.", agent_id)
        return agent_id

    def run(self) -> "Kernel":
        """
        Start the threaded tick loop (BOOTING → RUNNING if needed).

        Idempotent: calling ``run`` on an already-running kernel is a no-op.
        """
        if self._lifecycle.is_(KernelState.BOOTING):
            self.boot()
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
        Full teardown: stop the loop, the scheduler backend, then every worker
        (``AGENT_OFFLINE`` per agent). Leaves the kernel STOPPED.
        """
        self.stop()
        self._ctx.scheduler.stop()
        self._ctx.worker_runtime.shutdown()
        self._ctx.logger.info("Kernel shutdown complete.")

    # ------------------------------------------------------------------ #
    #  Work submission + execution
    # ------------------------------------------------------------------ #

    def submit(self, task: Task) -> None:
        """Add *task* to the graph and publish ``TASK_CREATED``."""
        self._ctx.graph.add_task(task)
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
        result = self._tick.run_once(n)
        self._maybe_autocheckpoint(n)
        return result

    def run_until_idle(self) -> List[ExecutionOutcome]:
        """
        Tick until the graph is fully drained, returning all outcomes.

        Terminates even if a tick makes no progress (e.g. ready tasks with no
        capable worker), so it never spins.
        """
        outcomes: List[ExecutionOutcome] = []
        while self._ctx.graph.has_active_work():
            result = self.tick()
            outcomes.extend(result.results)
            if result.dispatched == 0 and self._ctx.scheduler.inflight_count() == 0:
                break  # no placeable work and nothing outstanding
        return outcomes

    #: Backwards-compatible alias for :meth:`run_until_idle`.
    run_until_empty = run_until_idle

    # ------------------------------------------------------------------ #
    #  Checkpointing (v0.9)
    # ------------------------------------------------------------------ #

    def checkpoint(self) -> "Checkpoint":
        """Capture a serializable snapshot of the run's execution state."""
        from checkpoint.models import Checkpoint

        return Checkpoint(
            tick_count=self._tick_count,
            replans_done=(self._ctx.reflection.replans_done if self._ctx.reflection else 0),
            nodes=self._ctx.graph.snapshot(),
        )

    def save_checkpoint(self, store: "Optional[CheckpointStore]" = None) -> None:
        """Persist a checkpoint to *store* (or the context's configured store)."""
        store = store or self._ctx.checkpoint_store
        if store is None:
            raise RuntimeError("No checkpoint store configured or provided.")
        store.save(self.checkpoint())

    def restore(self, checkpoint: "Checkpoint") -> None:
        """
        Rebuild the run's execution state from *checkpoint*.

        The graph is repopulated (interrupted RUNNING tasks reset to re-run), the
        tick counter and reflection budget are restored. Register the same
        workers before calling this; then :meth:`run_until_idle` continues the run.
        """
        self._ctx.graph.restore(checkpoint.nodes)
        with self._tick_lock:
            self._tick_count = checkpoint.tick_count
        if self._ctx.reflection is not None:
            self._ctx.reflection.restore(checkpoint.replans_done)
        self._ctx.logger.info("Kernel restored from checkpoint: %s", checkpoint.summary())

    def load_checkpoint(self, store: "Optional[CheckpointStore]" = None) -> bool:
        """
        Load and apply the latest checkpoint from *store*. Returns ``True`` if a
        checkpoint was found and restored, ``False`` if there was none (fresh run).
        """
        store = store or self._ctx.checkpoint_store
        checkpoint = store.load() if store is not None else None
        if checkpoint is None:
            return False
        self.restore(checkpoint)
        return True

    def _maybe_autocheckpoint(self, tick_number: int) -> None:
        interval = self._ctx.settings.checkpoint_every_ticks
        store = self._ctx.checkpoint_store
        if store is None or interval <= 0 or tick_number % interval != 0:
            return
        try:
            self.save_checkpoint(store)
        except Exception:  # noqa: BLE001 — a failed checkpoint must not crash the run
            self._ctx.logger.exception("Auto-checkpoint failed at tick %d.", tick_number)

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
        workers = self._ctx.worker_runtime.all_workers()
        by_state = Counter(h.state.value for h in workers)
        return {
            "state": self.state.value,
            "tick_count": self._tick_count,
            "pending": len(self._ctx.graph.pending_tasks()),
            "inflight": self._ctx.scheduler.inflight_count(),
            "replans": self._ctx.reflection.replans_done if self._ctx.reflection else 0,
            "workers": {
                "total": len(workers),
                "idle": by_state.get("idle", 0),
                "busy": by_state.get("busy", 0),
                "failed": by_state.get("failed", 0),
                "offline": by_state.get("offline", 0),
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
    def graph(self):
        return self._ctx.graph

    @property
    def runtime(self):
        return self._ctx.worker_runtime

    @property
    def store(self):
        return self._ctx.result_store

    @property
    def task_queue(self) -> GraphTaskQueue:
        """
        The graph exposed through the ``AbstractTaskQueue`` port — the seeding
        surface the PlanningService and legacy callers already speak.
        """
        if self._task_queue_adapter is None:
            self._task_queue_adapter = GraphTaskQueue(self._ctx.graph)
        return self._task_queue_adapter

    def __repr__(self) -> str:
        return (
            f"Kernel(state={self.state.value}, "
            f"workers={len(self._ctx.worker_runtime.all_workers())}, "
            f"ticks={self._tick_count})"
        )


def build_kernel(settings: Optional[KernelSettings] = None) -> Kernel:
    """
    Factory returning a fully wired, in-memory :class:`Kernel`.

    For a distributed backend, build a :class:`KernelContext` with the relevant
    abstraction override and pass it to :class:`Kernel` directly.
    """
    return Kernel(KernelContext.in_memory(settings))
