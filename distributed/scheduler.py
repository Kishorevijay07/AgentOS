from __future__ import annotations

import logging
import time
from typing import Dict, Optional, Set
from uuid import UUID

from events.bus import AbstractEventBus
from events.event import Event
from events.event_type import EventType
from distributed.discovery import WorkerDirectory, WorkerPresence
from distributed.messages import ResultMessage, TaskMessage
from distributed.transport import Channels, Transport
from result_store import AbstractResultStore
from runtime.outcome import ExecutionOutcome
from scheduling.capability import CapabilityMatcher, DefaultCapabilityMatcher
from scheduling.retry import MaxAttemptsRetryPolicy, RetryPolicy
from task_graph.graph import AbstractTaskGraph


class DistributedScheduler:
    """
    Coordinator-side scheduler that dispatches work over the transport.

    It is the distributed twin of the in-process ``ExecutionScheduler``: it reads
    *ready* tasks from the Task Graph, matches them by **capability** against the
    :class:`WorkerDirectory` (records, not worker objects), and **sends a
    :class:`TaskMessage`** to the chosen worker's inbox. Results arrive
    asynchronously as :class:`ResultMessage`\\ s on the ``results`` topic, which
    the scheduler correlates back into the graph (complete / retry / fail).

    Because placement is by capability and dispatch is by message, the scheduler
    has **no idea whether a worker is local or on another machine** — the whole
    point of the distributed layer. It references only three abstractions: the
    graph, the directory, and the transport.

    Thread-safety: an ``RLock`` guards the in-flight table; result handling runs
    on the transport's delivery thread and is reentrant-safe with dispatch.
    """

    def __init__(
        self,
        graph: AbstractTaskGraph,
        directory: WorkerDirectory,
        transport: Transport,
        *,
        matcher: Optional[CapabilityMatcher] = None,
        retry_policy: Optional[RetryPolicy] = None,
        event_bus: Optional[AbstractEventBus] = None,
        result_store: Optional[AbstractResultStore] = None,
        task_timeout: Optional[float] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        import threading

        self._graph = graph
        self._directory = directory
        self._transport = transport
        self._matcher = matcher or DefaultCapabilityMatcher()
        self._retry = retry_policy or MaxAttemptsRetryPolicy()
        self._bus = event_bus
        self._store = result_store
        self._task_timeout = task_timeout
        self._log = logger or logging.getLogger("agentos.distributed")
        self._inflight: Dict[UUID, str] = {}  # task_id -> worker_id
        self._lock = threading.RLock()

    def start(self) -> None:
        """Subscribe to the results topic."""
        self._transport.subscribe(Channels.RESULTS, self._on_result)

    def stop(self) -> None:
        self._transport.unsubscribe(Channels.RESULTS, self._on_result)

    # ------------------------------------------------------------------ #
    #  Dispatch
    # ------------------------------------------------------------------ #

    def dispatch_ready(self) -> int:
        """
        Place each ready task on a capable, free worker and send it. Returns the
        number of tasks dispatched this pass.
        """
        dispatched = 0
        with self._lock:
            busy: Set[str] = set(self._inflight.values())
            for node in self._graph.ready_tasks():
                candidates = [
                    w for w in self._directory.available_workers()
                    if w.worker_id not in busy
                ]
                worker = self._matcher.match(node.required_capabilities, candidates)
                if worker is None:
                    continue
                busy.add(worker.worker_id)
                self._send(node.task, worker.worker_id)
                dispatched += 1
        return dispatched

    def run_until_idle(self, *, poll_interval: float = 0.01, timeout: float = 30.0) -> None:
        """
        Drive dispatch until the graph is drained.

        With a synchronous transport this returns after the work completes; with
        an asynchronous broker it polls while results are in flight (bounded by
        *timeout*) and stops when nothing ready can be placed and nothing is
        outstanding.
        """
        deadline = time.time() + timeout
        while self._graph.has_active_work():
            self._reap_lost_tasks()
            dispatched = self.dispatch_ready()
            with self._lock:
                waiting = len(self._inflight)
            if dispatched == 0:
                if waiting == 0:
                    break  # ready tasks (if any) have no capable worker
                if time.time() > deadline:
                    self._log.warning("run_until_idle timed out with %d in flight.", waiting)
                    break
                time.sleep(poll_interval)

    # ------------------------------------------------------------------ #
    #  Result handling
    # ------------------------------------------------------------------ #

    def _on_result(self, message) -> None:
        assert isinstance(message, ResultMessage)
        with self._lock:
            self._inflight.pop(message.task_id, None)

        node = self._graph.get_node(message.task_id)
        if node is None:
            return

        if self._store is not None:
            self._store.finish_execution(
                message.task_id, output=message.output,
                success=message.success, error=message.error,
            )

        if message.success:
            self._graph.mark_completed(message.task_id, execution_id=message.execution_id)
            self._publish(EventType.TASK_COMPLETED, message.task_id, message.worker_id)
        else:
            self._graph.mark_failed(message.task_id, message.error or "",
                                    execution_id=message.execution_id)
            self._publish(EventType.TASK_FAILED, message.task_id, message.worker_id)
            outcome = ExecutionOutcome(
                task_id=message.task_id, worker_id=message.worker_id,
                success=False, error=message.error, timed_out=message.timed_out,
            )
            if self._retry.should_retry(node.task, outcome):
                self._graph.reset_for_retry(message.task_id)
                self._log.info("Retrying task %s (attempt %d).", message.task_id, node.task.retry_count)

    # ------------------------------------------------------------------ #
    #  Crash handling
    # ------------------------------------------------------------------ #

    def _reap_lost_tasks(self) -> None:
        """Fail (and retry) tasks whose assigned worker went offline mid-flight."""
        with self._lock:
            lost = [
                (task_id, wid)
                for task_id, wid in self._inflight.items()
                if (info := self._directory.get(wid)) is None
                or info.presence != WorkerPresence.ONLINE
            ]
            for task_id, _ in lost:
                self._inflight.pop(task_id, None)
        for task_id, wid in lost:
            self._log.warning("Worker %s lost; failing task %s.", wid, task_id)
            node = self._graph.get_node(task_id)
            if node is None:
                continue
            self._graph.mark_failed(task_id, f"worker {wid} lost")
            outcome = ExecutionOutcome(task_id=task_id, worker_id=wid, success=False,
                                       error="worker lost")
            if self._retry.should_retry(node.task, outcome):
                self._graph.reset_for_retry(task_id)

    # ------------------------------------------------------------------ #
    #  Internals
    # ------------------------------------------------------------------ #

    def _send(self, task, worker_id: str) -> None:
        self._graph.mark_running(task.id, worker_id)
        self._inflight[task.id] = worker_id

        execution_id = None
        if self._store is not None:
            execution_id = self._store.start_execution(task.id, agent_id=worker_id).execution_id

        self._publish(EventType.TASK_ASSIGNED, task.id, worker_id)
        self._transport.publish(
            Channels.tasks_for(worker_id),
            TaskMessage(
                sender="scheduler", worker_id=worker_id, task=task,
                execution_id=execution_id, timeout=self._task_timeout,
            ),
        )

    def _publish(self, event_type: EventType, task_id, worker_id: str) -> None:
        if self._bus is None:
            return
        self._bus.publish(
            Event(type=event_type,
                  payload={"task_id": str(task_id), "worker_id": worker_id},
                  source="DistributedScheduler")
        )
