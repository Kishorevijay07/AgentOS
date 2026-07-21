"""Unit tests for ReflectionCoordinator — live-graph mutation + loop safety."""
from __future__ import annotations

from typing import List

import pytest

from events.bus import InMemoryEventBus
from events.event_type import EventType
from models.task import Task
from reflection.coordinator import ReflectionCoordinator
from reflection.models import (
    ProposedTask,
    ReflectionDecision,
    ReflectionRequest,
    ReflectionVerdict,
)
from reflection.reflector import Reflector
from runtime.outcome import ExecutionOutcome
from task_graph import InMemoryTaskGraph
from task_graph.state import NodeState


class _ScriptedReflector(Reflector):
    """Returns a fixed decision — lets tests drive the coordinator precisely."""

    def __init__(self, decision: ReflectionDecision) -> None:
        self._decision = decision
        self.seen: List[ReflectionRequest] = []

    def reflect(self, request: ReflectionRequest) -> ReflectionDecision:
        self.seen.append(request)
        return self._decision


def _replan(desc="Add tests", caps=("test",)) -> ReflectionDecision:
    return ReflectionDecision(
        verdict=ReflectionVerdict.REPLAN,
        reason="needs follow-up",
        new_tasks=[ProposedTask(description=desc, capabilities=list(caps))],
    )


def _completed_task(graph: InMemoryTaskGraph, desc="parent", caps=("code",)) -> Task:
    task = Task(description=desc, required_capabilities=list(caps))
    graph.add_task(task)
    graph.mark_running(task.id, "w1")
    graph.mark_completed(task.id)
    return task


def _outcome(task: Task, *, success=True, output="short") -> ExecutionOutcome:
    return ExecutionOutcome(task_id=task.id, worker_id="w1", success=success, output=output)


@pytest.fixture()
def graph() -> InMemoryTaskGraph:
    return InMemoryTaskGraph()


class TestReplan:
    def test_replan_injects_task_with_dependency_and_provenance(self, graph):
        bus = InMemoryEventBus()
        parent = _completed_task(graph)
        coord = ReflectionCoordinator(graph, _ScriptedReflector(_replan()), event_bus=bus)

        injected = coord.process([_outcome(parent)])

        assert len(injected) == 1
        node = graph.get_node(injected[0])
        assert node.description == "Add tests"
        assert parent.id in node.dependencies            # depends on the reflected task
        assert node.metadata["origin"] == "reflection"   # provenance stamped
        assert node.metadata["parent"] == str(parent.id)
        # New task is ready (its only dependency is already completed).
        assert node.state == NodeState.READY
        # Observers saw the injected work.
        created = [e for e in bus.history()
                   if e.type == EventType.TASK_CREATED and e.source == "Reflection"]
        assert len(created) == 1

    def test_accept_injects_nothing(self, graph):
        parent = _completed_task(graph)
        coord = ReflectionCoordinator(
            graph, _ScriptedReflector(ReflectionDecision.accept())
        )
        assert coord.process([_outcome(parent)]) == []


class TestLoopSafety:
    def test_budget_caps_total_injections(self, graph):
        p1 = _completed_task(graph, desc="p1")
        p2 = _completed_task(graph, desc="p2")
        coord = ReflectionCoordinator(graph, _ScriptedReflector(_replan()), max_replans=1)

        injected = coord.process([_outcome(p1), _outcome(p2)])

        assert len(injected) == 1          # second replan blocked by budget
        assert coord.replans_done == 1

    def test_reflection_origin_tasks_are_not_reflected_on(self, graph):
        reflector = _ScriptedReflector(_replan())
        coord = ReflectionCoordinator(graph, reflector)
        parent = _completed_task(graph)
        # Simulate an already-injected task and complete it.
        child_id = coord.process([_outcome(parent)])[0]
        graph.mark_running(child_id, "w1")
        graph.mark_completed(child_id)
        reflector.seen.clear()

        # An outcome for the injected task must NOT be reflected on again.
        child_outcome = ExecutionOutcome(task_id=child_id, worker_id="w1", success=True,
                                         output="short")
        assert coord.process([child_outcome]) == []
        assert reflector.seen == []  # reflector never even consulted

    def test_failed_outcomes_are_skipped(self, graph):
        reflector = _ScriptedReflector(_replan())
        coord = ReflectionCoordinator(graph, reflector)
        parent = _completed_task(graph)
        assert coord.process([_outcome(parent, success=False)]) == []
        assert reflector.seen == []  # failures are the RetryPolicy's job
