from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence
from uuid import UUID

from models.enums import Priority
from models.task import Task
from task_graph.errors import (
    CycleDetectedError,
    DuplicateTaskError,
    InvalidTransitionError,
    UnknownTaskError,
)
from task_graph.node import ExecutionAttempt, TaskNode
from task_graph.observers import GraphObserver
from task_graph.state import ACTIVE_STATES, NodeState, can_transition
from task_graph.visualize import GraphVisualizer, MermaidVisualizer

logger = logging.getLogger("agentos.task_graph")

# Priority ordering for ready-task selection (lower = more urgent). Accepts the
# str values Task stores (use_enum_values=True) as well as Priority enums.
_PRIORITY_ORDER: Dict[str, int] = {
    Priority.CRITICAL.value: 0,
    Priority.HIGH.value: 1,
    Priority.MEDIUM.value: 2,
    Priority.LOW.value: 3,
}


def _priority_key(node: TaskNode) -> int:
    p = node.priority
    return _PRIORITY_ORDER.get(p.value if isinstance(p, Priority) else str(p), 99)


class AbstractTaskGraph(ABC):
    """
    Contract for the executable task DAG.

    The Scheduler and the runtime depend on *this* interface, never on a
    concrete graph. That is the seam a future ``RedisTaskGraph`` /
    ``SQLTaskGraph`` (distributed, persistent, checkpointable) slots into with no
    consumer change.

    The interface is deliberately *readiness-centric*: the only question a
    scheduler ever asks is :meth:`ready_tasks`. Dependency reasoning lives
    entirely inside the implementation.
    """

    # --- structure -------------------------------------------------------
    @abstractmethod
    def add_task(self, task: Task) -> UUID: ...

    @abstractmethod
    def remove_task(self, task_id: UUID) -> None: ...

    @abstractmethod
    def add_dependency(self, task_id: UUID, depends_on: UUID) -> None: ...

    # --- lifecycle -------------------------------------------------------
    @abstractmethod
    def mark_running(self, task_id: UUID, worker_id: Optional[str] = None) -> None: ...

    @abstractmethod
    def mark_completed(self, task_id: UUID, *, execution_id: Optional[UUID] = None) -> None: ...

    @abstractmethod
    def mark_failed(self, task_id: UUID, error: str = "", *, execution_id: Optional[UUID] = None) -> None: ...

    @abstractmethod
    def cancel_task(self, task_id: UUID) -> None: ...

    @abstractmethod
    def reset_for_retry(self, task_id: UUID) -> None: ...

    # --- queries ---------------------------------------------------------
    @abstractmethod
    def ready_tasks(self) -> List[TaskNode]: ...

    @abstractmethod
    def completed_tasks(self) -> List[TaskNode]: ...

    @abstractmethod
    def pending_tasks(self) -> List[TaskNode]: ...

    @abstractmethod
    def failed_tasks(self) -> List[TaskNode]: ...

    @abstractmethod
    def get_node(self, task_id: UUID) -> Optional[TaskNode]: ...

    @abstractmethod
    def nodes(self) -> List[TaskNode]: ...

    @abstractmethod
    def detect_cycles(self) -> List[List[UUID]]: ...

    @abstractmethod
    def has_active_work(self) -> bool: ...

    @abstractmethod
    def visualize(self) -> str: ...


class InMemoryTaskGraph(AbstractTaskGraph):
    """
    Thread-safe, in-memory Directed Acyclic Graph of tasks.

    Invariants
    ----------
    * **Acyclic by construction** — every edge insertion is cycle-checked, so the
      graph is *never* in a cyclic state (rather than validated after the fact).
    * **Readiness is derived state** — a node is ``READY`` iff every dependency is
      ``COMPLETED``. Completing a node re-evaluates its children and unlocks any
      whose dependencies are now all satisfied, notifying observers.

    Thread safety
    -------------
    A single re-entrant lock guards all structure and state. Public methods take
    the lock; observer callbacks are invoked **after** the lock is released, on
    the caller's thread, so an observer can safely call back into the graph.

    Dependency injection
    --------------------
    ``visualizer`` (rendering strategy) and ``observers`` (notification sinks)
    are injected; both have sensible defaults.
    """

    def __init__(
        self,
        *,
        visualizer: Optional[GraphVisualizer] = None,
        observers: Optional[Sequence[GraphObserver]] = None,
    ) -> None:
        self._nodes: Dict[UUID, TaskNode] = {}
        self._lock = threading.RLock()
        self._visualizer = visualizer or MermaidVisualizer()
        self._observers: List[GraphObserver] = list(observers or [])

    # ------------------------------------------------------------------ #
    #  Observer registration
    # ------------------------------------------------------------------ #

    def register_observer(self, observer: GraphObserver) -> None:
        """Add an observer notified of ready/completed/failed transitions."""
        with self._lock:
            self._observers.append(observer)

    # ------------------------------------------------------------------ #
    #  Structure
    # ------------------------------------------------------------------ #

    def add_task(self, task: Task) -> UUID:
        """
        Insert *task* as a node and wire edges for any of its dependencies that
        are already present (and any existing nodes that depend on it).

        Returns the task id. Raises :class:`DuplicateTaskError` if already present
        and :class:`CycleDetectedError` if the resulting graph would be cyclic.
        """
        newly_ready: List[TaskNode] = []
        with self._lock:
            if task.id in self._nodes:
                raise DuplicateTaskError(f"Task {task.id} already in graph.")

            node = TaskNode(task=task)
            self._nodes[task.id] = node

            # Wire this node's declared dependencies to existing parents…
            for dep in task.dependencies:
                if dep in self._nodes:
                    self._nodes[dep].children.add(task.id)
            # …and wire existing nodes that declared a dependency on this one.
            for other in self._nodes.values():
                if task.id in other.dependencies:
                    node.children.add(other.task_id)

            cycle = self._find_cycle()
            if cycle:
                # Roll back: an added task must never leave a cyclic graph.
                del self._nodes[task.id]
                for parent in task.dependencies:
                    if parent in self._nodes:
                        self._nodes[parent].children.discard(task.id)
                for other in self._nodes.values():
                    other.children.discard(task.id)
                raise CycleDetectedError(cycle)

            self._recompute_state(node, newly_ready)

        self._emit_ready(newly_ready)
        logger.debug("Added task %s to graph.", task.id)
        return task.id

    def remove_task(self, task_id: UUID) -> None:
        """Remove a node and detach it from all parents and children."""
        with self._lock:
            node = self._require(task_id)
            for dep in node.dependencies:
                parent = self._nodes.get(dep)
                if parent:
                    parent.children.discard(task_id)
            for child_id in list(node.children):
                child = self._nodes.get(child_id)
                if child and task_id in child.dependencies:
                    child.dependencies.remove(task_id)
            del self._nodes[task_id]

    def add_dependency(self, task_id: UUID, depends_on: UUID) -> None:
        """
        Declare that *task_id* depends on *depends_on* (parent → child edge).

        Rejected with :class:`CycleDetectedError` if the edge would create a
        cycle. Adding a dependency to a ``READY`` node re-blocks it until the new
        parent completes — this is what makes dynamic replanning safe.
        """
        with self._lock:
            child = self._require(task_id)
            parent = self._require(depends_on)
            if depends_on in child.dependencies:
                return  # idempotent
            if task_id == depends_on:
                raise CycleDetectedError([task_id, task_id])

            child.dependencies.append(depends_on)
            parent.children.add(task_id)

            cycle = self._find_cycle()
            if cycle:
                child.dependencies.remove(depends_on)
                parent.children.discard(task_id)
                raise CycleDetectedError(cycle)

            # New dependency may re-block a ready node.
            if child.state in (NodeState.READY, NodeState.BLOCKED):
                self._demote_or_promote(child)

    # ------------------------------------------------------------------ #
    #  Lifecycle transitions
    # ------------------------------------------------------------------ #

    def mark_running(self, task_id: UUID, worker_id: Optional[str] = None) -> None:
        """Transition a ready node to RUNNING and open a history attempt."""
        with self._lock:
            node = self._require(task_id)
            self._transition(node, NodeState.RUNNING)
            node.assigned_worker = worker_id
            node.history.append(
                ExecutionAttempt(
                    attempt=len(node.history) + 1,
                    worker_id=worker_id,
                    started_at=datetime.now(timezone.utc),
                )
            )

    def mark_completed(self, task_id: UUID, *, execution_id: Optional[UUID] = None) -> None:
        """
        Complete a node and unlock any children whose dependencies are now met.
        """
        completed: TaskNode
        newly_ready: List[TaskNode] = []
        with self._lock:
            node = self._require(task_id)
            self._transition(node, NodeState.COMPLETED)
            self._close_attempt(node, success=True, execution_id=execution_id)
            completed = node
            for child_id in node.children:
                child = self._nodes[child_id]
                if child.state == NodeState.BLOCKED and self._deps_complete(child):
                    self._transition(child, NodeState.READY)
                    newly_ready.append(child)

        self._emit_completed(completed)
        self._emit_ready(newly_ready)

    def mark_failed(self, task_id: UUID, error: str = "", *, execution_id: Optional[UUID] = None) -> None:
        """
        Fail a node. Children stay BLOCKED (their dependency is unmet) and
        ``retry_count`` is incremented for retry policies to consult.
        """
        failed: TaskNode
        with self._lock:
            node = self._require(task_id)
            self._transition(node, NodeState.FAILED)
            self._close_attempt(node, success=False, error=error, execution_id=execution_id)
            node.task.retry_count += 1
            failed = node
        self._emit_failed(failed)

    def cancel_task(self, task_id: UUID) -> None:
        """Cancel a non-terminal node. Descendants remain blocked (dep unmet)."""
        with self._lock:
            node = self._require(task_id)
            self._transition(node, NodeState.CANCELLED)

    def reset_for_retry(self, task_id: UUID) -> None:
        """Move a FAILED node back to READY/BLOCKED so it can run again."""
        newly_ready: List[TaskNode] = []
        with self._lock:
            node = self._require(task_id)
            if node.state != NodeState.FAILED:
                raise InvalidTransitionError(
                    f"Only FAILED tasks can be reset; {task_id} is {node.state.value}."
                )
            if self._deps_complete(node):
                self._transition(node, NodeState.READY)
                newly_ready.append(node)
            else:
                # deps regressed — go via READY? No: FAILED→BLOCKED not allowed,
                # so first move to READY then re-block through the state machine.
                self._transition(node, NodeState.READY)
                self._transition(node, NodeState.BLOCKED)
        self._emit_ready(newly_ready)

    # ------------------------------------------------------------------ #
    #  Queries
    # ------------------------------------------------------------------ #

    def ready_tasks(self) -> List[TaskNode]:
        """Return READY nodes, most-urgent first. The scheduler's only question."""
        with self._lock:
            ready = [n for n in self._nodes.values() if n.state == NodeState.READY]
        return sorted(ready, key=_priority_key)

    def completed_tasks(self) -> List[TaskNode]:
        with self._lock:
            return [n for n in self._nodes.values() if n.state == NodeState.COMPLETED]

    def pending_tasks(self) -> List[TaskNode]:
        """Nodes that still represent outstanding work (BLOCKED/READY/RUNNING)."""
        with self._lock:
            return [n for n in self._nodes.values() if n.state in ACTIVE_STATES]

    def failed_tasks(self) -> List[TaskNode]:
        with self._lock:
            return [n for n in self._nodes.values() if n.state == NodeState.FAILED]

    def get_node(self, task_id: UUID) -> Optional[TaskNode]:
        with self._lock:
            return self._nodes.get(task_id)

    def nodes(self) -> List[TaskNode]:
        with self._lock:
            return list(self._nodes.values())

    def has_active_work(self) -> bool:
        with self._lock:
            return any(n.state in ACTIVE_STATES for n in self._nodes.values())

    def detect_cycles(self) -> List[List[UUID]]:
        """Return a list containing one cyclic path if the graph is cyclic, else []."""
        with self._lock:
            cycle = self._find_cycle()
        return [cycle] if cycle else []

    def visualize(self) -> str:
        with self._lock:
            snapshot = list(self._nodes.values())
        return self._visualizer.render(snapshot)

    def __len__(self) -> int:
        with self._lock:
            return len(self._nodes)

    # ------------------------------------------------------------------ #
    #  Internals (all callers already hold the lock)
    # ------------------------------------------------------------------ #

    def _require(self, task_id: UUID) -> TaskNode:
        node = self._nodes.get(task_id)
        if node is None:
            raise UnknownTaskError(f"No task {task_id} in graph.")
        return node

    def _deps_complete(self, node: TaskNode) -> bool:
        # A dependency that is not (yet) in the graph counts as unsatisfied, so a
        # task with an unresolved dependency stays BLOCKED rather than wrongly
        # becoming READY.
        return all(
            d in self._nodes and self._nodes[d].state == NodeState.COMPLETED
            for d in node.dependencies
        )

    def _recompute_state(self, node: TaskNode, newly_ready: List[TaskNode]) -> None:
        """Set a freshly-added node to READY or BLOCKED based on its deps."""
        if self._deps_complete(node):
            if node.state == NodeState.BLOCKED:
                self._transition(node, NodeState.READY)
                newly_ready.append(node)

    def _demote_or_promote(self, node: TaskNode) -> None:
        """After an edge change, re-block a READY node or ready a BLOCKED one."""
        if self._deps_complete(node):
            if node.state == NodeState.BLOCKED:
                self._transition(node, NodeState.READY)
        else:
            if node.state == NodeState.READY:
                self._transition(node, NodeState.BLOCKED)

    def _transition(self, node: TaskNode, target: NodeState) -> None:
        if node.state == target:
            return
        if not can_transition(node.state, target):
            raise InvalidTransitionError(
                f"Illegal node transition {node.state.value} → {target.value} "
                f"for task {node.task_id}."
            )
        node.state = target

    @staticmethod
    def _close_attempt(
        node: TaskNode,
        *,
        success: bool,
        error: Optional[str] = None,
        execution_id: Optional[UUID] = None,
    ) -> None:
        if not node.history:
            return
        attempt = node.history[-1]
        attempt.finished_at = datetime.now(timezone.utc)
        attempt.success = success
        attempt.error = error
        attempt.execution_id = execution_id

    def _find_cycle(self) -> List[UUID]:
        """Return one cyclic path (list of ids) if the graph is cyclic, else []."""
        WHITE, GREY, BLACK = 0, 1, 2
        colour: Dict[UUID, int] = {nid: WHITE for nid in self._nodes}
        stack: List[UUID] = []

        def dfs(nid: UUID) -> List[UUID]:
            colour[nid] = GREY
            stack.append(nid)
            for parent in self._nodes[nid].dependencies:
                if parent not in colour:
                    continue
                if colour[parent] == GREY:
                    # Found a back-edge — extract the cycle from the stack.
                    idx = stack.index(parent)
                    return stack[idx:] + [parent]
                if colour[parent] == WHITE:
                    found = dfs(parent)
                    if found:
                        return found
            stack.pop()
            colour[nid] = BLACK
            return []

        for nid in self._nodes:
            if colour[nid] == WHITE:
                found = dfs(nid)
                if found:
                    return found
        return []

    # ------------------------------------------------------------------ #
    #  Observer emission (called without the lock held)
    # ------------------------------------------------------------------ #

    def _emit_ready(self, nodes: List[TaskNode]) -> None:
        for node in nodes:
            self._notify("on_ready", node)

    def _emit_completed(self, node: TaskNode) -> None:
        self._notify("on_completed", node)

    def _emit_failed(self, node: TaskNode) -> None:
        self._notify("on_failed", node)

    def _notify(self, hook: str, node: TaskNode) -> None:
        for observer in list(self._observers):
            try:
                getattr(observer, hook)(node)
            except Exception:  # noqa: BLE001 — one bad observer must not break others
                logger.exception("Graph observer %r failed on %s.", observer, hook)
