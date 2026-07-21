from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from uuid import UUID

from events.bus import AbstractEventBus
from events.event import Event
from events.event_type import EventType
from models.task import Task
from result_store import AbstractResultStore, LogLevel
from runtime.outcome import ExecutionOutcome
from scheduling.backend import DispatchBackend, LocalDispatchBackend
from scheduling.capability import CapabilityMatcher, DefaultCapabilityMatcher
from scheduling.retry import MaxAttemptsRetryPolicy, RetryPolicy
from task_graph.graph import AbstractTaskGraph
from task_graph.node import TaskNode


class AbstractExecutionScheduler(ABC):
    """Port for the execution scheduler — placement + reconciliation."""

    @abstractmethod
    def schedule_wave(self) -> int:
        """Assign one wave of ready tasks. Returns tasks dispatched."""

    @abstractmethod
    def run_until_idle(self) -> None:
        """Drive waves until the graph has no runnable work left."""


class ExecutionScheduler(AbstractExecutionScheduler):
    """
    **The** AgentOS scheduler — one placement/reconciliation loop for every
    execution world.

    Since v0.7 there is a single scheduler implementation. What used to be three
    overlapping loops (the kernel's ``Dispatcher``, the in-process
    ``ExecutionScheduler``, and the ``DistributedScheduler``) is now this class
    plus a pluggable :class:`~scheduling.backend.DispatchBackend`:

    * ``ExecutionScheduler(graph, runtime)`` — local, in-process execution
      (sugar for a :class:`LocalDispatchBackend`);
    * ``ExecutionScheduler(graph, backend=TransportDispatchBackend(...))`` —
      distributed execution over the message transport.

    The loop itself is backend-agnostic: read ready tasks from the graph, match
    them to candidate workers by **capability**, fire-and-forget through the
    backend, and reconcile each :class:`ExecutionOutcome` back into the graph
    (complete / retry / fail) when the backend delivers it — immediately for
    local execution, asynchronously for distributed.

    Thread-safety: the in-flight table and outcome buffer are lock-guarded;
    outcome delivery may arrive on a transport thread while a wave is being
    scheduled.
    """

    def __init__(
        self,
        graph: AbstractTaskGraph,
        runtime=None,
        *,
        backend: Optional[DispatchBackend] = None,
        matcher: Optional[CapabilityMatcher] = None,
        retry_policy: Optional[RetryPolicy] = None,
        event_bus: Optional[AbstractEventBus] = None,
        result_store: Optional[AbstractResultStore] = None,
        task_timeout: Optional[float] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        if backend is None:
            if runtime is None:
                raise ValueError("Provide either a worker `runtime` or a `backend`.")
            backend = LocalDispatchBackend(runtime)
        self._backend = backend
        self._backend.set_outcome_handler(self._on_outcome)

        self._graph = graph
        self._matcher = matcher or DefaultCapabilityMatcher()
        self._retry = retry_policy or MaxAttemptsRetryPolicy()
        self._bus = event_bus
        self._store = result_store
        self._task_timeout = task_timeout
        self._log = logger or logging.getLogger("agentos.scheduling")

        self._lock = threading.RLock()
        self._inflight: Dict[UUID, str] = {}          # task_id -> worker_id
        self._outcomes: List[ExecutionOutcome] = []   # buffered since last drain

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Start the backend (e.g. subscribe to the results topic)."""
        self._backend.start()

    def stop(self) -> None:
        self._backend.stop()

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def schedule_wave(self) -> int:
        """
        Place one wave of ready tasks. Returns the number dispatched.

        Candidates are re-queried per task, so a synchronous (local) backend —
        whose worker is free again the moment ``dispatch`` returns — can serve
        several ready tasks in a single wave, while an asynchronous backend's
        busy workers are excluded via the in-flight table until their results
        arrive. A ready task with no capable free worker is skipped (it stays
        READY for a later wave).
        """
        dispatched = 0
        for node in self._graph.ready_tasks():
            with self._lock:
                if node.task_id in self._inflight:
                    continue  # already sent (async backend, result pending)
                busy = set(self._inflight.values())
            candidates = [c for c in self._backend.candidates() if c.worker_id not in busy]
            chosen = self._matcher.match(node.required_capabilities, candidates)
            if chosen is None:
                continue
            self._dispatch(node, chosen.worker_id)
            dispatched += 1
        return dispatched

    def drain_outcomes(self) -> List[ExecutionOutcome]:
        """Return (and clear) every outcome delivered since the last drain."""
        with self._lock:
            outcomes, self._outcomes = self._outcomes, []
            return outcomes

    def inflight_count(self) -> int:
        with self._lock:
            return len(self._inflight)

    def run_until_idle(self, *, poll_interval: float = 0.01, timeout: float = 30.0) -> None:
        """
        Drive waves until no runnable progress remains (never spins forever).

        With a local backend this returns once the graph drains; with an
        asynchronous backend it polls while results are in flight, bounded by
        *timeout*.
        """
        deadline = time.time() + timeout
        while self._graph.has_active_work():
            self.reap_lost_tasks()
            dispatched = self.schedule_wave()
            if dispatched == 0:
                if self.inflight_count() == 0:
                    break  # nothing placeable and nothing outstanding
                if time.time() > deadline:
                    self._log.warning(
                        "run_until_idle timed out with %d in flight.", self.inflight_count()
                    )
                    break
                time.sleep(poll_interval)

    def reap_lost_tasks(self) -> List[UUID]:
        """Fail (and maybe retry) in-flight tasks whose worker was lost."""
        with self._lock:
            lost = self._backend.lost_tasks(dict(self._inflight))
            for task_id in lost:
                self._inflight.pop(task_id, None)
        for task_id in lost:
            node = self._graph.get_node(task_id)
            if node is None:
                continue
            worker_id = node.assigned_worker or "?"
            self._log.warning("Worker %s lost; failing task %s.", worker_id, task_id)
            if self._store is not None:
                self._safe_finish_store(task_id, output=None, success=False,
                                        error=f"worker {worker_id} lost")
            self._graph.mark_failed(task_id, f"worker {worker_id} lost")
            self._publish(EventType.TASK_FAILED, task_id, worker_id)
            outcome = ExecutionOutcome(task_id=task_id, worker_id=worker_id,
                                       success=False, error="worker lost")
            if self._retry.should_retry(node.task, outcome):
                self._graph.reset_for_retry(task_id)
        return lost

    # ------------------------------------------------------------------ #
    #  Dispatch + reconciliation
    # ------------------------------------------------------------------ #

    def _dispatch(self, node: TaskNode, worker_id: str) -> None:
        task = node.task
        self._graph.mark_running(node.task_id, worker_id)
        with self._lock:
            self._inflight[task.id] = worker_id

        execution_id = None
        if self._store is not None:
            execution_id = self._store.start_execution(task.id, agent_id=worker_id).execution_id
            self._store.add_log(task.id, LogLevel.INFO,
                                f"Task started on {worker_id}.", source="Scheduler")

        self._publish(EventType.TASK_ASSIGNED, task.id, worker_id)
        self._publish(EventType.TASK_STARTED, task.id, worker_id)

        # Fire and forget — the outcome arrives via _on_outcome (immediately for
        # a local backend, later for a transport backend).
        self._backend.dispatch(task, worker_id,
                               execution_id=execution_id, timeout=self._task_timeout)

    def _on_outcome(self, outcome: ExecutionOutcome) -> None:
        with self._lock:
            self._inflight.pop(outcome.task_id, None)
            self._outcomes.append(outcome)

        node = self._graph.get_node(outcome.task_id)
        if node is None:
            return

        if self._store is not None:
            self._store.add_log(
                outcome.task_id,
                LogLevel.INFO if outcome.success else LogLevel.ERROR,
                "Task completed successfully." if outcome.success
                else f"Task failed: {outcome.error}",
                source="Scheduler",
            )
            self._safe_finish_store(outcome.task_id, output=outcome.output,
                                    success=outcome.success, error=outcome.error)

        if outcome.success:
            self._graph.mark_completed(outcome.task_id, execution_id=outcome.execution_id)
            self._publish(EventType.TASK_COMPLETED, outcome.task_id, outcome.worker_id)
        else:
            self._graph.mark_failed(outcome.task_id, outcome.error or "",
                                    execution_id=outcome.execution_id)
            self._publish(EventType.TASK_FAILED, outcome.task_id, outcome.worker_id)
            if self._retry.should_retry(node.task, outcome):
                self._graph.reset_for_retry(outcome.task_id)
                self._log.info("Retrying task %s (attempt %d).",
                               outcome.task_id, node.task.retry_count)

    # ------------------------------------------------------------------ #
    #  Internals
    # ------------------------------------------------------------------ #

    def _safe_finish_store(self, task_id: UUID, **kwargs) -> None:
        """Close the trace if it is still open (idempotent for lost+late results)."""
        try:
            self._store.finish_execution(task_id, **kwargs)
        except (KeyError, RuntimeError):
            pass  # never opened, or already closed by the reaper

    def _publish(self, event_type: EventType, task_id, worker_id: str) -> None:
        if self._bus is None:
            return
        self._bus.publish(
            Event(type=event_type,
                  payload={"task_id": str(task_id), "worker_id": worker_id},
                  source="Scheduler")
        )
