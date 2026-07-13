from __future__ import annotations


class WorkerError(Exception):
    """Base class for every error raised by the worker runtime."""


class DuplicateWorkerError(WorkerError):
    """A worker with the same id is already registered."""


class WorkerNotFoundError(WorkerError):
    """An operation referenced a worker id the runtime does not know."""


class WorkerBusyError(WorkerError):
    """A task was dispatched to a worker that is not IDLE."""


class InvalidWorkerStateError(WorkerError):
    """A worker was asked to make a lifecycle transition that is not permitted."""


class WorkerTimeoutError(WorkerError):
    """A task exceeded its execution timeout."""
