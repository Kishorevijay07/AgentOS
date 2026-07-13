from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import List, Optional, Set

from events.bus import AbstractEventBus
from events.event import Event
from events.event_type import EventType
from result_store import AbstractResultStore, LogLevel
from runtime.runtime import AbstractWorkerRuntime
from scheduling.capability import CapabilityMatcher, DefaultCapabilityMatcher
from scheduling.retry import MaxAttemptsRetryPolicy, RetryPolicy
from task_graph.graph import AbstractTaskGraph
from task_graph.node import TaskNode


class AbstractExecutionScheduler(ABC):
    """Port for the execution scheduler — placement + reconciliation."""

    @abstractmethod
    def schedule_wave(self) -> int:
        """Assign and execute one wave of ready tasks. Returns tasks dispatched."""

    @abstractmethod
    def run_until_idle(self) -> None:
        """Drive waves until the graph has no runnable work left."""


class ExecutionScheduler(AbstractExecutionScheduler):
    """
    Operating-system-style scheduler tying the Task Graph to the Worker Runtime.

    It reads *ready* tasks from the graph, reads *available* workers from the
    runtime, matches them by **capability** (never by task type or worker class),
    dispatches through the runtime, and reconciles each outcome back into the
    graph — completing, retrying, or failing. It understands neither how a worker
    runs a task (that's the runtime) nor how dependencies unblock (that's the
    graph); it only places work.

    Collaborators (all injected — DIP):

    * ``graph`` — the source of ready tasks and the sink for outcomes;
    * ``runtime`` — the pool of workers and the execution mechanism;
    * ``matcher`` — the capability-placement :class:`CapabilityMatcher` strategy;
    * ``retry_policy`` — the :class:`RetryPolicy` strategy;
    * ``event_bus`` / ``result_store`` — optional observability sinks.

    Concurrency model
    -----------------
    One wave assigns at most one task per worker, so a wave's tasks can run in
    parallel across distinct workers (the runtime's executor provides the
    parallelism). Completing a task unblocks its dependents, which surface in the
    next wave — the same tick discipline the Kernel uses.
    """

    def __init__(
        self,
        graph: AbstractTaskGraph,
        runtime: AbstractWorkerRuntime,
        *,
        matcher: Optional[CapabilityMatcher] = None,
        retry_policy: Optional[RetryPolicy] = None,
        event_bus: Optional[AbstractEventBus] = None,
        result_store: Optional[AbstractResultStore] = None,
        task_timeout: Optional[float] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._graph = graph
        self._runtime = runtime
        self._matcher = matcher or DefaultCapabilityMatcher()
        self._retry = retry_policy or MaxAttemptsRetryPolicy()
        self._bus = event_bus
        self._store = result_store
        self._task_timeout = task_timeout
        self._log = logger or logging.getLogger("agentos.scheduling")

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def schedule_wave(self) -> int:
        """
        Place and execute one wave of ready tasks.

        Returns the number of tasks dispatched. A task with no capable idle
        worker is skipped (it stays READY for a later wave).
        """
        dispatched = 0
        used: Set[str] = set()

        for node in self._graph.ready_tasks():
            candidates = [
                h for h in self._runtime.available_workers() if h.worker_id not in used
            ]
            handle = self._matcher.match(node.required_capabilities, candidates)
            if handle is None:
                continue
            used.add(handle.worker_id)
            self._dispatch(node, handle.worker_id)
            dispatched += 1
        return dispatched

    def run_until_idle(self) -> None:
        """Drive waves until no runnable progress remains (never spins)."""
        while self._graph.has_active_work():
            if self.schedule_wave() == 0:
                # Ready tasks (if any) have no capable idle worker → cannot proceed.
                break

    # ------------------------------------------------------------------ #
    #  Internals
    # ------------------------------------------------------------------ #

    def _dispatch(self, node: TaskNode, worker_id: str) -> None:
        task = node.task
        self._graph.mark_running(node.task_id, worker_id)
        self._publish(EventType.TASK_ASSIGNED, task.id, worker_id)
        self._publish(EventType.TASK_STARTED, task.id, worker_id)

        execution_id = None
        if self._store is not None:
            record = self._store.start_execution(task.id, agent_id=worker_id)
            execution_id = record.execution_id
            self._store.add_log(task.id, LogLevel.INFO,
                                f"Dispatched to {worker_id}.", source="Scheduler")

        outcome = self._runtime.execute_task(
            worker_id, task, timeout=self._task_timeout, execution_id=execution_id
        )

        if self._store is not None:
            self._store.add_log(
                task.id,
                LogLevel.INFO if outcome.success else LogLevel.ERROR,
                "Completed." if outcome.success else f"Failed: {outcome.error}",
                source="Scheduler",
            )
            self._store.finish_execution(
                task.id, output=outcome.output, success=outcome.success, error=outcome.error
            )

        if outcome.success:
            self._graph.mark_completed(task.id, execution_id=execution_id)
            self._publish(EventType.TASK_COMPLETED, task.id, worker_id)
        else:
            self._graph.mark_failed(task.id, outcome.error or "", execution_id=execution_id)
            self._publish(EventType.TASK_FAILED, task.id, worker_id)
            if self._retry.should_retry(task, outcome):
                # Re-ready the task (FAILED → READY) for a subsequent wave.
                self._graph.reset_for_retry(task.id)
                self._log.info("Retrying task %s (attempt %d).", task.id, task.retry_count)

    def _publish(self, event_type: EventType, task_id, worker_id: str) -> None:
        if self._bus is None:
            return
        self._bus.publish(
            Event(
                type=event_type,
                payload={"task_id": str(task_id), "worker_id": worker_id},
                source="Scheduler",
            )
        )
