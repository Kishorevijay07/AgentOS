"""
runtime — the AgentOS Worker Runtime (Sprint 6).

Manages the worker pool as a resource: lifecycle, execution with timeouts,
failure isolation, health checks, metrics, and graceful shutdown. Workers never
manage themselves — the runtime drives every transition.

The runtime depends on the :class:`Worker` *Protocol*, not on the ``agents``
package, so any object with the worker shape (all ``BaseAgent`` subclasses, plus
third-party/plugin workers) is managed with zero changes.

Quick start
-----------
>>> from runtime import DefaultWorkerRuntime
>>> from agents.coding import CodingAgent
>>> rt = DefaultWorkerRuntime()
>>> wid = rt.register_worker(CodingAgent())
>>> outcome = rt.execute_task(wid, task)     # timed, isolated, metered
>>> rt.worker_metrics(wid).success_rate
"""

from runtime.errors import (
    DuplicateWorkerError,
    InvalidWorkerStateError,
    WorkerBusyError,
    WorkerError,
    WorkerNotFoundError,
    WorkerTimeoutError,
)
from runtime.executor import TaskExecutor, ThreadPoolTaskExecutor
from runtime.handle import WorkerHandle
from runtime.lifecycle import WorkerState
from runtime.metrics import WorkerMetrics
from runtime.outcome import ExecutionOutcome
from runtime.runtime import AbstractWorkerRuntime, DefaultWorkerRuntime
from runtime.worker import Worker

__all__ = [
    "Worker",
    "WorkerState",
    "WorkerHandle",
    "WorkerMetrics",
    "ExecutionOutcome",
    "TaskExecutor",
    "ThreadPoolTaskExecutor",
    "AbstractWorkerRuntime",
    "DefaultWorkerRuntime",
    "WorkerError",
    "DuplicateWorkerError",
    "WorkerNotFoundError",
    "WorkerBusyError",
    "InvalidWorkerStateError",
    "WorkerTimeoutError",
]
