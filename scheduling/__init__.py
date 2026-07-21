"""
scheduling — the AgentOS Execution Scheduler (Sprint 7).

An operating-system-style scheduler that ties the Task Graph to the Worker
Runtime. It reads ready tasks from the graph, matches them to available workers
by **capability**, dispatches through the runtime, and reconciles outcomes
(complete / retry / fail) back into the graph. It knows neither worker
implementations nor how dependencies unblock — only placement.

Quick start
-----------
>>> from scheduling import ExecutionScheduler
>>> scheduler = ExecutionScheduler(graph, runtime)
>>> scheduler.run_until_idle()
"""

from scheduling.backend import (
    DispatchBackend,
    LocalDispatchBackend,
    TransportDispatchBackend,
)
from scheduling.capability import (
    CapabilityMatcher,
    DefaultCapabilityMatcher,
    HasCapabilities,
)
from scheduling.retry import MaxAttemptsRetryPolicy, RetryPolicy
from scheduling.scheduler import AbstractExecutionScheduler, ExecutionScheduler

__all__ = [
    "AbstractExecutionScheduler",
    "ExecutionScheduler",
    "DispatchBackend",
    "LocalDispatchBackend",
    "TransportDispatchBackend",
    "CapabilityMatcher",
    "DefaultCapabilityMatcher",
    "HasCapabilities",
    "RetryPolicy",
    "MaxAttemptsRetryPolicy",
]
