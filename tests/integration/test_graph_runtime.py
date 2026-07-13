"""
Integration test — Task Graph drives the live runtime.

Proves the boundary: the Dispatcher/Kernel execute a DAG through the
GraphTaskQueue adapter, pulling only ``ready_tasks()`` and unlocking dependents
on completion — the Scheduler never reasons about dependencies.
"""
from __future__ import annotations

from agents.coding import CodingAgent
from agents.documentation import DocumentationAgent
from agents.research import ResearchAgent
from agents.testing import TestingAgent
from events.event_type import EventType
from kernel import Kernel, KernelContext
from planning import TemplatePlanner
from planning.models import Goal
from task_graph import (
    EventBusGraphObserver,
    GraphTaskQueue,
    InMemoryTaskGraph,
    PlanGraphBuilder,
)
from task_graph.state import NodeState


def _kernel_with_graph(graph):
    context = KernelContext.in_memory(task_queue=GraphTaskQueue(graph))
    kernel = Kernel(context).boot()
    for agent in (ResearchAgent(), CodingAgent(), TestingAgent(), DocumentationAgent()):
        kernel.register_agent(agent)
    return kernel


class TestGraphDrivenExecution:
    def test_dag_executes_end_to_end(self):
        plan = TemplatePlanner().plan(Goal(description="Build a REST API for a blog"))
        graph = PlanGraphBuilder().build(plan)

        kernel = _kernel_with_graph(graph)
        results = kernel.run_until_idle()

        # All five DAG nodes ran to completion, in dependency order.
        assert len(results) == 5
        assert all(r.success for r in results)
        assert len(graph.completed_tasks()) == 5
        assert graph.has_active_work() is False

    def test_graph_publishes_ready_events_via_observer(self):
        plan = TemplatePlanner().plan(Goal(description="Analyze data"))
        graph = InMemoryTaskGraph()
        kernel = _kernel_with_graph(graph)
        graph.register_observer(EventBusGraphObserver(kernel.bus))

        # Populate after wiring the observer so we capture the unlock events.
        PlanGraphBuilder().build(plan, graph=graph)
        kernel.run_until_idle()

        ready_events = [e for e in kernel.bus.history() if e.type == EventType.TASK_READY]
        # 5 linear steps → each becomes ready exactly once.
        assert len(ready_events) == 5
        assert all(e.source == "TaskGraph" for e in ready_events)

    def test_failed_dependency_blocks_downstream(self):
        # A node whose capability no worker satisfies fails; its dependents
        # must never run.
        plan = TemplatePlanner().plan(Goal(description="x"))
        graph = PlanGraphBuilder().build(plan)

        # Kernel with NO workers → the first ready task can't be matched and fails.
        context = KernelContext.in_memory(task_queue=GraphTaskQueue(graph))
        kernel = Kernel(context).boot()

        kernel.run_until_idle()

        # First node failed; everything downstream stayed blocked (never ran).
        assert len(graph.failed_tasks()) == 1
        blocked = [n for n in graph.nodes() if n.state == NodeState.BLOCKED]
        assert len(blocked) == 4
