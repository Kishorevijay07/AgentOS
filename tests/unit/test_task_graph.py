"""Unit tests for the InMemoryTaskGraph DAG engine."""
from __future__ import annotations

from typing import List, Optional

import pytest

from models.enums import Priority
from models.task import Task
from task_graph.errors import (
    CycleDetectedError,
    DuplicateTaskError,
    InvalidTransitionError,
    UnknownTaskError,
)
from task_graph.graph import InMemoryTaskGraph
from task_graph.node import TaskNode
from task_graph.state import NodeState


def mk(desc: str, deps: Optional[list] = None, caps=None, priority=Priority.MEDIUM) -> Task:
    return Task(
        description=desc,
        dependencies=deps or [],
        required_capabilities=caps or [],
        priority=priority,
    )


@pytest.fixture()
def graph() -> InMemoryTaskGraph:
    return InMemoryTaskGraph()


class TestStructure:
    def test_add_task_and_lookup(self, graph):
        a = mk("a")
        graph.add_task(a)
        assert graph.get_node(a.id).description == "a"
        assert len(graph) == 1

    def test_duplicate_rejected(self, graph):
        a = mk("a")
        graph.add_task(a)
        with pytest.raises(DuplicateTaskError):
            graph.add_task(a)

    def test_unknown_task_raises(self, graph):
        with pytest.raises(UnknownTaskError):
            graph.mark_completed(mk("ghost").id)


class TestReadiness:
    def test_source_is_ready_dependent_is_blocked(self, graph):
        a = mk("a")
        b = mk("b", deps=[a.id])
        graph.add_task(a)
        graph.add_task(b)

        ready_ids = [n.task_id for n in graph.ready_tasks()]
        assert ready_ids == [a.id]
        assert graph.get_node(b.id).state == NodeState.BLOCKED

    def test_completing_parent_unlocks_child(self, graph):
        a = mk("a")
        b = mk("b", deps=[a.id])
        graph.add_task(a)
        graph.add_task(b)

        graph.mark_running(a.id, "w1")
        graph.mark_completed(a.id)

        ready_ids = [n.task_id for n in graph.ready_tasks()]
        assert ready_ids == [b.id]

    def test_child_waits_for_all_parents(self, graph):
        a, b = mk("a"), mk("b")
        c = mk("c", deps=[a.id, b.id])
        for t in (a, b, c):
            graph.add_task(t)

        graph.mark_running(a.id)
        graph.mark_completed(a.id)
        assert graph.get_node(c.id).state == NodeState.BLOCKED  # b still pending

        graph.mark_running(b.id)
        graph.mark_completed(b.id)
        assert graph.get_node(c.id).state == NodeState.READY

    def test_ready_tasks_sorted_by_priority(self, graph):
        low = mk("low", priority=Priority.LOW)
        crit = mk("crit", priority=Priority.CRITICAL)
        graph.add_task(low)
        graph.add_task(crit)
        assert [n.description for n in graph.ready_tasks()] == ["crit", "low"]


class TestCycles:
    def test_add_dependency_cycle_rejected(self, graph):
        a, b = mk("a"), mk("b")
        graph.add_task(a)
        graph.add_task(b)
        graph.add_dependency(a.id, b.id)  # a depends on b
        with pytest.raises(CycleDetectedError):
            graph.add_dependency(b.id, a.id)  # would close the loop

    def test_self_dependency_rejected(self, graph):
        a = mk("a")
        graph.add_task(a)
        with pytest.raises(CycleDetectedError):
            graph.add_dependency(a.id, a.id)

    def test_mutual_dependency_on_add_is_rolled_back(self, graph):
        a, b = mk("a"), mk("b")
        a.dependencies.append(b.id)
        b.dependencies.append(a.id)
        graph.add_task(a)  # b absent → a blocked, no cycle
        with pytest.raises(CycleDetectedError):
            graph.add_task(b)  # closes the loop → rejected + rolled back
        assert graph.get_node(b.id) is None
        assert len(graph) == 1

    def test_detect_cycles_empty_for_dag(self, graph):
        a = mk("a")
        b = mk("b", deps=[a.id])
        graph.add_task(a)
        graph.add_task(b)
        assert graph.detect_cycles() == []


class TestFailureAndRetry:
    def test_failure_blocks_children_and_bumps_retry(self, graph):
        a = mk("a")
        b = mk("b", deps=[a.id])
        graph.add_task(a)
        graph.add_task(b)

        graph.mark_running(a.id)
        graph.mark_failed(a.id, "boom")

        node = graph.get_node(a.id)
        assert node.state == NodeState.FAILED
        assert node.retry_count == 1
        assert node.history[-1].success is False
        assert graph.get_node(b.id).state == NodeState.BLOCKED
        assert graph.ready_tasks() == []

    def test_reset_for_retry_readies_failed_task(self, graph):
        a = mk("a")
        graph.add_task(a)
        graph.mark_running(a.id)
        graph.mark_failed(a.id, "boom")
        graph.reset_for_retry(a.id)
        assert graph.get_node(a.id).state == NodeState.READY

    def test_reset_non_failed_raises(self, graph):
        a = mk("a")
        graph.add_task(a)
        with pytest.raises(InvalidTransitionError):
            graph.reset_for_retry(a.id)


class TestTransitionsAndCancel:
    def test_illegal_transition_rejected(self, graph):
        a = mk("a")
        graph.add_task(a)  # READY
        with pytest.raises(InvalidTransitionError):
            graph.mark_completed(a.id)  # READY → COMPLETED is not allowed

    def test_cancel(self, graph):
        a = mk("a")
        graph.add_task(a)
        graph.cancel_task(a.id)
        assert graph.get_node(a.id).state == NodeState.CANCELLED
        assert graph.has_active_work() is False


class TestDynamicDependency:
    def test_adding_dependency_reblocks_ready_node(self, graph):
        a, b = mk("a"), mk("b")
        graph.add_task(a)
        graph.add_task(b)
        assert graph.get_node(b.id).state == NodeState.READY
        graph.add_dependency(b.id, a.id)  # now b depends on a
        assert graph.get_node(b.id).state == NodeState.BLOCKED


class _RecordingObserver:
    def __init__(self) -> None:
        self.ready: List[str] = []
        self.completed: List[str] = []
        self.failed: List[str] = []

    def on_ready(self, node: TaskNode) -> None:
        self.ready.append(node.description)

    def on_completed(self, node: TaskNode) -> None:
        self.completed.append(node.description)

    def on_failed(self, node: TaskNode) -> None:
        self.failed.append(node.description)


class TestObservers:
    def test_notifications_fire(self, graph):
        obs = _RecordingObserver()
        graph.register_observer(obs)

        a = mk("a")
        b = mk("b", deps=[a.id])
        graph.add_task(a)  # a becomes ready → on_ready("a")
        graph.add_task(b)  # b blocked → no notification
        graph.mark_running(a.id)
        graph.mark_completed(a.id)  # on_completed("a") + on_ready("b")

        assert obs.ready == ["a", "b"]
        assert obs.completed == ["a"]

    def test_failure_notifies(self, graph):
        obs = _RecordingObserver()
        graph.register_observer(obs)
        a = mk("a")
        graph.add_task(a)
        graph.mark_running(a.id)
        graph.mark_failed(a.id, "x")
        assert obs.failed == ["a"]
