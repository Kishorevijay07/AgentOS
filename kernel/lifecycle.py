from __future__ import annotations

import threading
from enum import Enum


class KernelState(str, Enum):
    """
    Runtime lifecycle states for the AgentOS Kernel — like Docker's container
    lifecycle.

    Transitions
    -----------
    BOOTING   ──► RUNNING        (boot completes)
    RUNNING   ──► PAUSED         (pause())
    PAUSED    ──► RUNNING        (resume())
    RUNNING   ──► STOPPING       (stop())
    PAUSED    ──► STOPPING       (stop())
    STOPPING  ──► STOPPED        (teardown complete)
    STOPPED   ──► ∅              (terminal)
    """

    BOOTING = "booting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"


# Valid transitions: from → {allowed targets}
_TRANSITIONS: dict[KernelState, set[KernelState]] = {
    KernelState.BOOTING: {KernelState.RUNNING, KernelState.STOPPING},
    KernelState.RUNNING: {KernelState.PAUSED, KernelState.STOPPING},
    KernelState.PAUSED: {KernelState.RUNNING, KernelState.STOPPING},
    KernelState.STOPPING: {KernelState.STOPPED},
    KernelState.STOPPED: set(),
}


class Lifecycle:
    """
    Thread-safe guard for the Kernel's :class:`KernelState`.

    Mirrors the worker-lifecycle state machine (``agents/worker.py``): a single
    ``Lock`` protects the state, and :meth:`transition` refuses any move not
    whitelisted in ``_TRANSITIONS`` — so an illegal request (e.g. reviving a
    STOPPED kernel) fails fast with a clear error instead of leaving the runtime
    in an impossible state.

    A fresh Lifecycle starts in ``BOOTING``.
    """

    def __init__(self) -> None:
        self._state: KernelState = KernelState.BOOTING
        self._lock = threading.Lock()

    @property
    def state(self) -> KernelState:
        """Current lifecycle state (thread-safe read)."""
        with self._lock:
            return self._state

    def is_(self, *states: KernelState) -> bool:
        """Return ``True`` if the current state is one of *states*."""
        with self._lock:
            return self._state in states

    def transition(self, target: KernelState) -> KernelState:
        """
        Move to *target*, or raise ``RuntimeError`` if the move is illegal.

        Returns the new state on success.
        """
        with self._lock:
            allowed = _TRANSITIONS.get(self._state, set())
            if target not in allowed:
                raise RuntimeError(
                    f"Invalid kernel state transition: {self._state} → {target}. "
                    f"Allowed: {allowed or '{} (terminal)'}"
                )
            self._state = target
            return self._state

    def __repr__(self) -> str:
        return f"Lifecycle(state={self.state.value!r})"
