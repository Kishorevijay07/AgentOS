from __future__ import annotations

from enum import Enum


class WorkerState(str, Enum):
    """
    Runtime-owned lifecycle of a managed worker.

    *Workers never manage themselves* — the :class:`~runtime.runtime.WorkerRuntime`
    drives every transition. This is the authoritative view the runtime keeps in
    the worker's :class:`~runtime.handle.WorkerHandle`, distinct from any state a
    worker might track internally.

    Transitions
    -----------
    INITIALIZING ──► IDLE            (initialize() succeeded)
    INITIALIZING ──► FAILED          (initialize() raised)
    IDLE         ──► BUSY            (task dispatched)
    IDLE         ──► PAUSED          (pause())
    BUSY         ──► IDLE            (task finished)
    BUSY         ──► FAILED          (crash / timeout during execution)
    PAUSED       ──► IDLE            (resume())
    FAILED       ──► IDLE            (recovered / reset)
    any(non-terminal) ──► OFFLINE    (unregister / shutdown / missed heartbeat)
    """

    INITIALIZING = "initializing"
    IDLE = "idle"
    BUSY = "busy"
    PAUSED = "paused"
    FAILED = "failed"
    OFFLINE = "offline"


_TRANSITIONS: dict[WorkerState, set[WorkerState]] = {
    WorkerState.INITIALIZING: {WorkerState.IDLE, WorkerState.FAILED, WorkerState.OFFLINE},
    WorkerState.IDLE: {WorkerState.BUSY, WorkerState.PAUSED, WorkerState.FAILED, WorkerState.OFFLINE},
    WorkerState.BUSY: {WorkerState.IDLE, WorkerState.FAILED, WorkerState.OFFLINE},
    WorkerState.PAUSED: {WorkerState.IDLE, WorkerState.OFFLINE},
    WorkerState.FAILED: {WorkerState.IDLE, WorkerState.OFFLINE},
    WorkerState.OFFLINE: set(),
}


def can_transition(src: WorkerState, dst: WorkerState) -> bool:
    """Return ``True`` if moving from *src* to *dst* is permitted."""
    return dst in _TRANSITIONS.get(src, set())
