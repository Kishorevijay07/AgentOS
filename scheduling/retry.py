from __future__ import annotations

from typing import Protocol, runtime_checkable

from models.task import Task
from runtime.outcome import ExecutionOutcome


@runtime_checkable
class RetryPolicy(Protocol):
    """
    Strategy deciding whether a failed task should be retried.

    Keeps retry *policy* out of scheduling *mechanism*: the scheduler asks the
    policy and either re-readies the task in the graph or leaves it failed. Swap
    for exponential-backoff, error-class-aware, or budget-aware policies without
    changing the scheduler.
    """

    def should_retry(self, task: Task, outcome: ExecutionOutcome) -> bool:
        """Return ``True`` if *task* should be retried after *outcome*."""
        ...


class MaxAttemptsRetryPolicy:
    """
    Retry up to a fixed number of attempts.

    ``task.retry_count`` is incremented by the graph on each failure, so this
    policy simply compares it to ``max_attempts``. Timeouts are optionally
    excluded (a hung worker often re-hangs); by default they are retried like any
    other failure.
    """

    def __init__(self, max_attempts: int = 3, *, retry_on_timeout: bool = True) -> None:
        self._max_attempts = max_attempts
        self._retry_on_timeout = retry_on_timeout

    def should_retry(self, task: Task, outcome: ExecutionOutcome) -> bool:
        if outcome.timed_out and not self._retry_on_timeout:
            return False
        return task.retry_count < self._max_attempts
