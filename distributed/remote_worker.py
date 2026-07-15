from __future__ import annotations

import logging
from typing import Optional

from distributed.heartbeat import HeartbeatEmitter
from distributed.messages import (
    DeregisterMessage,
    RegisterMessage,
    ResultMessage,
    TaskMessage,
)
from distributed.transport import Channels, Transport
from runtime.runtime import AbstractWorkerRuntime, DefaultWorkerRuntime
from runtime.worker import Worker

logger = logging.getLogger("agentos.distributed")


class RemoteWorkerNode:
    """
    A worker-side daemon: hosts a :class:`Worker` and executes tasks that arrive
    over the transport.

    This is where "the worker runs on another machine" becomes concrete. The node
    reuses the in-process :class:`~runtime.runtime.DefaultWorkerRuntime`
    (Sprint 6) to actually run tasks — so timeouts, isolation, and metrics come
    for free — and wraps it with transport plumbing:

    * on :meth:`start` it **registers** (announces capabilities) and begins
      **heartbeating**, then subscribes to its own task inbox;
    * each :class:`TaskMessage` is executed by the local runtime and answered with
      a structured :class:`ResultMessage`;
    * on :meth:`stop` it **deregisters** gracefully and tears the runtime down.

    The node never calls the scheduler and the scheduler never calls the node —
    all coordination is messages. Run one of these per process/container/pod.
    """

    def __init__(
        self,
        worker: Worker,
        transport: Transport,
        *,
        worker_id: Optional[str] = None,
        runtime: Optional[AbstractWorkerRuntime] = None,
        heartbeat_interval: float = 5.0,
        address: Optional[str] = None,
        default_timeout: Optional[float] = None,
    ) -> None:
        self._worker = worker
        self._transport = transport
        self._worker_id = worker_id or type(worker).__name__
        self._runtime = runtime or DefaultWorkerRuntime(default_timeout=default_timeout)
        self._address = address
        self._heartbeat = HeartbeatEmitter(
            transport, self._worker_id, interval=heartbeat_interval,
            state_provider=self._current_state,
        )
        self._task_topic = Channels.tasks_for(self._worker_id)

    @property
    def worker_id(self) -> str:
        return self._worker_id

    def start(self, *, start_heartbeat: bool = True) -> None:
        """Register the worker, subscribe to its inbox, and begin heartbeating."""
        self._runtime.register_worker(self._worker, worker_id=self._worker_id)
        self._transport.subscribe(self._task_topic, self._on_task)
        self._transport.publish(
            Channels.REGISTER,
            RegisterMessage(
                sender=self._worker_id,
                worker_id=self._worker_id,
                capabilities=list(self._worker.capabilities),
                address=self._address,
            ),
        )
        if start_heartbeat:
            self._heartbeat.start()
        logger.info("RemoteWorkerNode %s started.", self._worker_id)

    def stop(self) -> None:
        """Deregister gracefully and tear down."""
        self._heartbeat.stop()
        self._transport.unsubscribe(self._task_topic, self._on_task)
        self._transport.publish(
            Channels.DEREGISTER,
            DeregisterMessage(sender=self._worker_id, worker_id=self._worker_id),
        )
        self._runtime.shutdown()
        logger.info("RemoteWorkerNode %s stopped.", self._worker_id)

    def beat(self) -> None:
        """Emit a single heartbeat (deterministic control for tests / tick loops)."""
        self._heartbeat.beat_once()

    # ------------------------------------------------------------------ #
    #  Message handling
    # ------------------------------------------------------------------ #

    def _on_task(self, message) -> None:
        assert isinstance(message, TaskMessage)
        if message.worker_id != self._worker_id:
            return  # not addressed to us
        outcome = self._runtime.execute_task(
            self._worker_id, message.task,
            timeout=message.timeout, execution_id=message.execution_id,
        )
        self._transport.publish(
            Channels.RESULTS,
            ResultMessage(
                sender=self._worker_id,
                worker_id=self._worker_id,
                task_id=outcome.task_id,
                success=outcome.success,
                output=outcome.output,
                error=outcome.error,
                duration_seconds=outcome.duration_seconds,
                timed_out=outcome.timed_out,
                execution_id=outcome.execution_id,
            ),
        )

    def _current_state(self) -> str:
        try:
            return self._runtime.get_worker(self._worker_id).state.value
        except Exception:  # noqa: BLE001
            return "unknown"
