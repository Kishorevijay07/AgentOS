from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Callable, List, Mapping, Optional, Sequence
from uuid import UUID

from models.task import Task
from runtime.outcome import ExecutionOutcome
from scheduling.capability import HasCapabilities

logger = logging.getLogger("agentos.scheduling")

#: Callback the scheduler registers to receive execution outcomes.
OutcomeHandler = Callable[[ExecutionOutcome], None]


class DispatchBackend(ABC):
    """
    The seam between *placement* and *execution* — the one abstraction that
    unifies AgentOS's schedulers.

    The :class:`~scheduling.scheduler.ExecutionScheduler` decides **where** work
    goes (capability matching, retries, graph reconciliation); a backend decides
    **how it gets there and runs**:

    * :class:`LocalDispatchBackend` — a direct call into the in-process
      :class:`~runtime.runtime.AbstractWorkerRuntime`; the outcome is delivered
      synchronously, before :meth:`dispatch` returns.
    * :class:`TransportDispatchBackend` — a :class:`TaskMessage` over the
      distributed transport; the outcome arrives later as a ``ResultMessage``.

    The scheduler cannot tell the difference — it registers one outcome handler
    and treats every dispatch as fire-and-forget. That indifference *is* the
    location transparency the distributed runtime promises.
    """

    def __init__(self) -> None:
        self._handler: Optional[OutcomeHandler] = None

    def set_outcome_handler(self, handler: OutcomeHandler) -> None:
        """Register the callback invoked once per completed execution."""
        self._handler = handler

    def _deliver(self, outcome: ExecutionOutcome) -> None:
        if self._handler is None:
            logger.warning("Outcome for task %s dropped: no handler set.", outcome.task_id)
            return
        self._handler(outcome)

    # ------------------------------------------------------------------ #
    #  Contract
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Acquire resources / subscriptions. Default: no-op."""

    def stop(self) -> None:
        """Release resources / subscriptions. Default: no-op."""

    @abstractmethod
    def candidates(self) -> Sequence[HasCapabilities]:
        """Workers currently able to accept a task (capability view only)."""

    @abstractmethod
    def dispatch(
        self,
        task: Task,
        worker_id: str,
        *,
        execution_id: Optional[UUID] = None,
        timeout: Optional[float] = None,
    ) -> None:
        """Send *task* to *worker_id*. The outcome arrives via the handler."""

    def lost_tasks(self, inflight: Mapping[UUID, str]) -> List[UUID]:
        """
        Given the scheduler's in-flight map (``task_id → worker_id``), return
        task ids whose worker has been lost (crashed / gone offline). Local
        execution can't lose a worker mid-call, so the default is none.
        """
        return []


class LocalDispatchBackend(DispatchBackend):
    """
    Executes tasks on the in-process worker runtime.

    ``dispatch`` runs the task synchronously through
    :meth:`AbstractWorkerRuntime.execute_task` (timeouts, isolation, metrics)
    and delivers the outcome to the handler **before returning** — the
    degenerate, zero-latency case of the fire-and-forget contract.
    """

    def __init__(self, runtime) -> None:  # AbstractWorkerRuntime (kept untyped to avoid cycle)
        super().__init__()
        self._runtime = runtime

    @property
    def runtime(self):
        """The wrapped worker runtime (exposed for health checks / metrics)."""
        return self._runtime

    def candidates(self) -> Sequence[HasCapabilities]:
        return self._runtime.available_workers()

    def dispatch(
        self,
        task: Task,
        worker_id: str,
        *,
        execution_id: Optional[UUID] = None,
        timeout: Optional[float] = None,
    ) -> None:
        outcome = self._runtime.execute_task(
            worker_id, task, timeout=timeout, execution_id=execution_id
        )
        self._deliver(outcome)


class TransportDispatchBackend(DispatchBackend):
    """
    Dispatches tasks as messages over the distributed transport.

    ``dispatch`` publishes a ``TaskMessage`` to the worker's inbox topic and
    returns immediately; when the worker's ``ResultMessage`` arrives on the
    results topic it is converted to an :class:`ExecutionOutcome` and delivered
    to the handler. ``lost_tasks`` reports in-flight tasks whose worker
    disappeared from the directory, so the scheduler can fail/retry them.
    """

    def __init__(self, directory, transport, *, sender: str = "scheduler") -> None:
        # WorkerDirectory / Transport — imported lazily to keep scheduling/
        # importable without the distributed package.
        super().__init__()
        self._directory = directory
        self._transport = transport
        self._sender = sender

    def start(self) -> None:
        from distributed.transport import Channels

        self._transport.subscribe(Channels.RESULTS, self._on_result)

    def stop(self) -> None:
        from distributed.transport import Channels

        self._transport.unsubscribe(Channels.RESULTS, self._on_result)

    def candidates(self) -> Sequence[HasCapabilities]:
        return self._directory.available_workers()

    def dispatch(
        self,
        task: Task,
        worker_id: str,
        *,
        execution_id: Optional[UUID] = None,
        timeout: Optional[float] = None,
    ) -> None:
        from distributed.messages import TaskMessage
        from distributed.transport import Channels

        self._transport.publish(
            Channels.tasks_for(worker_id),
            TaskMessage(
                sender=self._sender, worker_id=worker_id, task=task,
                execution_id=execution_id, timeout=timeout,
            ),
        )

    def lost_tasks(self, inflight: Mapping[UUID, str]) -> List[UUID]:
        from distributed.discovery import WorkerPresence

        lost: List[UUID] = []
        for task_id, worker_id in inflight.items():
            info = self._directory.get(worker_id)
            if info is None or info.presence != WorkerPresence.ONLINE:
                lost.append(task_id)
        return lost

    # ------------------------------------------------------------------ #
    #  Message handling
    # ------------------------------------------------------------------ #

    def _on_result(self, message) -> None:
        from distributed.messages import ResultMessage

        assert isinstance(message, ResultMessage)
        self._deliver(
            ExecutionOutcome(
                task_id=message.task_id,
                worker_id=message.worker_id,
                success=message.success,
                output=message.output,
                error=message.error,
                duration_seconds=message.duration_seconds,
                timed_out=message.timed_out,
                execution_id=message.execution_id,
            )
        )
