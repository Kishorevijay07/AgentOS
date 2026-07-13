from __future__ import annotations

from enum import Enum


class NodeState(str, Enum):
    """
    Lifecycle of a single task node in the DAG — an OS-process analogue.

    Transitions
    -----------
    BLOCKED   ──► READY        (all dependencies completed)
    READY     ──► RUNNING      (scheduler dispatched it)
    READY     ──► BLOCKED      (a new dependency was added — dynamic replanning)
    RUNNING   ──► COMPLETED    (worker succeeded)
    RUNNING   ──► FAILED       (worker raised / returned failure)
    FAILED    ──► READY        (retry / reset)
    any(non-terminal) ──► CANCELLED

    ``COMPLETED`` and ``CANCELLED`` are terminal. ``FAILED`` is *settled* but not
    terminal — it can be reset to ``READY`` for a retry.
    """

    BLOCKED = "blocked"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Legal transitions: from → {allowed targets}.
_TRANSITIONS: dict[NodeState, set[NodeState]] = {
    NodeState.BLOCKED: {NodeState.READY, NodeState.CANCELLED},
    NodeState.READY: {NodeState.RUNNING, NodeState.BLOCKED, NodeState.CANCELLED},
    NodeState.RUNNING: {NodeState.COMPLETED, NodeState.FAILED, NodeState.CANCELLED},
    NodeState.FAILED: {NodeState.READY, NodeState.CANCELLED},
    NodeState.COMPLETED: set(),
    NodeState.CANCELLED: set(),
}

#: States in which a node still represents outstanding work.
ACTIVE_STATES: frozenset[NodeState] = frozenset(
    {NodeState.BLOCKED, NodeState.READY, NodeState.RUNNING}
)

#: States a node can no longer leave (except FAILED, which may be retried).
TERMINAL_STATES: frozenset[NodeState] = frozenset(
    {NodeState.COMPLETED, NodeState.CANCELLED}
)


def can_transition(src: NodeState, dst: NodeState) -> bool:
    """Return ``True`` if moving from *src* to *dst* is permitted."""
    return dst in _TRANSITIONS.get(src, set())
