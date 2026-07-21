from __future__ import annotations

import itertools
import logging
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import UUID

from events.bus import AbstractEventBus
from events.event import Event
from events.event_type import EventType
from models.task import Task
from runtime.errors import (
    DuplicateWorkerError,
    WorkerBusyError,
    WorkerNotFoundError,
)
from runtime.executor import TaskExecutor, ThreadPoolTaskExecutor
from runtime.handle import WorkerHandle
from runtime.lifecycle import WorkerState
from runtime.metrics import WorkerMetrics
from runtime.outcome import ExecutionOutcome
from runtime.worker import Worker

logger = logging.getLogger("agentos.runtime")


class AbstractWorkerRuntime(ABC):
    """
    Port for the worker runtime — the resource manager for the worker pool.

    The Execution Scheduler depends on *this* interface and only its
    capability-typed views (``available_workers`` returns handles whose
    ``capabilities`` it can match); it never constructs, inspects, or manages a
    worker itself. A future ``RemoteWorkerRuntime`` (RPC to a worker fleet) is a
    drop-in replacement.
    """

    @abstractmethod
    def register_worker(self, worker: Worker, *, worker_id: Optional[str] = None) -> str: ...

    @abstractmethod
    def unregister_worker(self, worker_id: str) -> None: ...

    @abstractmethod
    def get_worker(self, worker_id: str) -> WorkerHandle: ...

    @abstractmethod
    def execute_task(self, worker_id: str, task: Task, *, timeout: Optional[float] = None,
                     execution_id: Optional[UUID] = None) -> ExecutionOutcome: ...

    @abstractmethod
    def available_workers(self) -> List[WorkerHandle]: ...

    @abstractmethod
    def all_workers(self) -> List[WorkerHandle]: ...

    @abstractmethod
    def worker_metrics(self, worker_id: str) -> WorkerMetrics: ...

    @abstractmethod
    def worker_status(self, worker_id: str) -> dict: ...

    @abstractmethod
    def health_check(self) -> List[str]: ...

    @abstractmethod
    def shutdown(self) -> None: ...


class DefaultWorkerRuntime(AbstractWorkerRuntime):
    """
    In-process worker runtime.

    Owns a thread-safe pool of :class:`WorkerHandle`\\ s and runs tasks through an
    injected :class:`TaskExecutor` (default: a :class:`ThreadPoolTaskExecutor`,
    a small **factory** default). Responsibilities: lifecycle, execution with
    timeout, failure isolation, metrics, health checks, and graceful shutdown.

    Thread safety
    -------------
    A runtime-level lock guards the handle registry; each worker has its own
    lock guarding its state and metrics. Execution runs **without** the registry
    lock held, so independent workers execute in parallel (bounded by the
    executor's pool) — the runtime is genuinely concurrent, not a global mutex.

    Dependency injection
    --------------------
    ``executor`` (isolation strategy), ``event_bus`` (observability),
    ``default_timeout`` and ``heartbeat_timeout_seconds`` (policy) are all
    injected; sensible defaults are provided so the common case is one line.
    """

    def __init__(
        self,
        *,
        executor: Optional[TaskExecutor] = None,
        event_bus: Optional[AbstractEventBus] = None,
        default_timeout: Optional[float] = None,
        heartbeat_timeout_seconds: float = 90.0,
        logger_: Optional[logging.Logger] = None,
    ) -> None:
        self._handles: Dict[str, WorkerHandle] = {}
        self._lock = threading.RLock()
        self._executor: TaskExecutor = executor or ThreadPoolTaskExecutor()
        self._bus = event_bus
        self._default_timeout = default_timeout
        self._heartbeat_timeout = heartbeat_timeout_seconds
        self._log = logger_ or logger
        self._counters: Dict[str, "itertools.count[int]"] = {}

    # ------------------------------------------------------------------ #
    #  Registration & lifecycle
    # ------------------------------------------------------------------ #

    def register_worker(self, worker: Worker, *, worker_id: Optional[str] = None) -> str:
        """
        Admit *worker*, initialise it, and return its assigned id.

        A failure inside ``initialize`` is isolated: the worker is registered in
        ``FAILED`` state rather than propagating and taking down registration.
        """
        with self._lock:
            wid = worker_id or self._next_id(type(worker).__name__)
            if wid in self._handles:
                raise DuplicateWorkerError(f"Worker id {wid!r} already registered.")
            handle = WorkerHandle(wid, worker)
            self._handles[wid] = handle

        with handle.lock:
            try:
                worker.initialize()
                handle.transition(WorkerState.IDLE)
                handle.touch_heartbeat()
            except Exception as exc:  # noqa: BLE001 — isolate init failure
                handle.transition(WorkerState.FAILED)
                handle.metrics.last_error = f"initialize failed: {exc}"
                self._log.exception("Worker %s failed to initialize.", wid)

        self._publish(EventType.AGENT_ONLINE, wid, {"capabilities": list(handle.capabilities)})
        self._log.info("Registered worker %s (caps=%s).", wid, handle.capabilities)
        return wid

    def unregister_worker(self, worker_id: str) -> None:
        """Shut a worker down and remove it from the pool (→ OFFLINE)."""
        handle = self.get_worker(worker_id)
        with handle.lock:
            self._safe_shutdown(handle)
        with self._lock:
            self._handles.pop(worker_id, None)
        self._publish(EventType.AGENT_OFFLINE, worker_id, {})

    def get_worker(self, worker_id: str) -> WorkerHandle:
        with self._lock:
            handle = self._handles.get(worker_id)
        if handle is None:
            raise WorkerNotFoundError(f"No worker {worker_id!r} registered.")
        return handle

    def pause_worker(self, worker_id: str) -> None:
        handle = self.get_worker(worker_id)
        with handle.lock:
            handle.transition(WorkerState.PAUSED)
            handle.worker.pause()

    def resume_worker(self, worker_id: str) -> None:
        handle = self.get_worker(worker_id)
        with handle.lock:
            handle.worker.resume()
            handle.transition(WorkerState.IDLE)

    def recover_worker(self, worker_id: str) -> None:
        """Bring a FAILED worker back to IDLE (operator- or policy-driven)."""
        handle = self.get_worker(worker_id)
        with handle.lock:
            handle.transition(WorkerState.IDLE)

    # ------------------------------------------------------------------ #
    #  Execution
    # ------------------------------------------------------------------ #

    def execute_task(
        self,
        worker_id: str,
        task: Task,
        *,
        timeout: Optional[float] = None,
        execution_id: Optional[UUID] = None,
    ) -> ExecutionOutcome:
        """
        Run *task* on the given worker under a timeout, in isolation.

        Never raises for a *task* failure (crash or timeout) — those are captured
        in the returned :class:`ExecutionOutcome`, so one bad task can neither
        crash the runtime nor the scheduler. Raises only for *programming*
        errors: :class:`WorkerNotFoundError`, :class:`WorkerBusyError`.
        """
        handle = self.get_worker(worker_id)

        with handle.lock:
            if handle.state != WorkerState.IDLE:
                raise WorkerBusyError(
                    f"Worker {worker_id} is {handle.state.value}, not IDLE."
                )
            handle.transition(WorkerState.BUSY)
            handle.current_task = task.id

        started = time.perf_counter()
        output: object = None
        error: Optional[str] = None
        success = False
        timed_out = False

        try:
            output = self._executor.run(lambda: handle.worker.execute(task),
                                        timeout=timeout if timeout is not None else self._default_timeout)
            success = True
        except TimeoutError as exc:
            error, timed_out = str(exc), True
            self._log.warning("Worker %s timed out on task %s.", worker_id, task.id)
        except Exception as exc:  # noqa: BLE001 — isolate worker/task failure
            error = str(exc)
            self._log.warning("Worker %s failed task %s: %s", worker_id, task.id, exc)

        duration = time.perf_counter() - started
        now = datetime.now(timezone.utc)

        with handle.lock:
            handle.metrics.record(
                success=success, duration_seconds=duration, timed_out=timed_out,
                error=error, at=now,
            )
            handle.current_task = None
            handle.touch_heartbeat(now)
            # Only release a worker that is still BUSY. If it was moved concurrently
            # (e.g. runtime.shutdown() set it OFFLINE, or it was paused) while the
            # task ran outside the lock, respect that state rather than forcing a
            # (possibly illegal) transition back to IDLE.
            if handle.state == WorkerState.BUSY:
                # A timeout leaves the worker suspect → FAILED; a plain task error
                # leaves the worker healthy → IDLE.
                handle.transition(WorkerState.FAILED if timed_out else WorkerState.IDLE)

        return ExecutionOutcome(
            task_id=task.id, worker_id=worker_id, success=success, output=output,
            error=error, duration_seconds=duration, timed_out=timed_out,
            execution_id=execution_id,
        )

    # ------------------------------------------------------------------ #
    #  Views & metrics
    # ------------------------------------------------------------------ #

    def available_workers(self) -> List[WorkerHandle]:
        """Handles currently IDLE — the only view the scheduler needs."""
        with self._lock:
            return [h for h in self._handles.values() if h.state == WorkerState.IDLE]

    def all_workers(self) -> List[WorkerHandle]:
        with self._lock:
            return list(self._handles.values())

    def worker_metrics(self, worker_id: str) -> WorkerMetrics:
        handle = self.get_worker(worker_id)
        with handle.lock:
            return handle.metrics.model_copy(deep=True)

    def worker_status(self, worker_id: str) -> dict:
        handle = self.get_worker(worker_id)
        with handle.lock:
            return handle.status()

    # ------------------------------------------------------------------ #
    #  Health & shutdown
    # ------------------------------------------------------------------ #

    def health_check(self) -> List[str]:
        """
        Actively probe IDLE workers and age out unresponsive ones.

        Calls each idle worker's ``heartbeat()``; if it raises or the last
        heartbeat is older than the threshold, the worker is moved to ``FAILED``.
        Returns the ids that were marked unhealthy this pass.
        """
        failed: List[str] = []
        now = datetime.now(timezone.utc)
        for handle in self.all_workers():
            with handle.lock:
                if handle.state != WorkerState.IDLE:
                    continue
                try:
                    handle.worker.heartbeat()
                    handle.touch_heartbeat(now)
                except Exception as exc:  # noqa: BLE001
                    handle.transition(WorkerState.FAILED)
                    handle.metrics.last_error = f"heartbeat failed: {exc}"
                    failed.append(handle.worker_id)
                    continue
                age = (now - handle.last_heartbeat).total_seconds()
                if age > self._heartbeat_timeout:
                    handle.transition(WorkerState.FAILED)
                    failed.append(handle.worker_id)
        if failed:
            self._log.warning("health_check marked workers unhealthy: %s", failed)
        return failed

    def shutdown(self) -> None:
        """Gracefully shut every worker down, then release the executor."""
        for handle in self.all_workers():
            with handle.lock:
                self._safe_shutdown(handle)
            self._publish(EventType.AGENT_OFFLINE, handle.worker_id, {})
        self._executor.shutdown(wait=True)
        self._log.info("Worker runtime shut down.")

    # ------------------------------------------------------------------ #
    #  Internals
    # ------------------------------------------------------------------ #

    def _safe_shutdown(self, handle: WorkerHandle) -> None:
        """Best-effort worker teardown; caller holds handle.lock."""
        if handle.state == WorkerState.OFFLINE:
            return
        try:
            handle.worker.shutdown()
        except Exception:  # noqa: BLE001
            self._log.exception("Worker %s raised during shutdown.", handle.worker_id)
        handle.transition(WorkerState.OFFLINE)

    def _next_id(self, class_name: str) -> str:
        counter = self._counters.setdefault(class_name, itertools.count(1))
        return f"{class_name}-{next(counter)}"

    def _publish(self, event_type: EventType, worker_id: str, extra: dict) -> None:
        if self._bus is None:
            return
        self._bus.publish(
            Event(type=event_type, payload={"agent_id": worker_id, **extra}, source="WorkerRuntime")
        )
