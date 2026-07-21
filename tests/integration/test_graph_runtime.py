"""
Integration test — Task Graph drives the live Kernel runtime (v0.7).

Proves the boundary: the Kernel's unified scheduler pulls only ``ready_tasks()``
and unlocks dependents on completion — dependency reasoning never leaves the
graph, and unplaceable work is left READY (never mis-routed or spuriously
failed).
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
    InMemoryTaskGraph,
    PlanGraphBuilder,
)
from task_graph.state import NodeState


def _kernel_with_graph(graph):
    context = KernelContext.in_memory(graph=graph)
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
        kernel.shutdown()

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
        kernel.shutdown()

    def test_no_capable_worker_leaves_work_ready_not_failed(self):
        # A kernel with NO workers cannot place anything: the ready source task
        # must remain READY (available for a future worker), downstream stays
        # BLOCKED, and nothing is spuriously failed.
        plan = TemplatePlanner().plan(Goal(description="x"))
        graph = PlanGraphBuilder().build(plan)

        context = KernelContext.in_memory(graph=graph)
        kernel = Kernel(context).boot()

        results = kernel.run_until_idle()

        assert results == []
        assert len(graph.completed_tasks()) == 0
        assert len(graph.failed_tasks()) == 0
        ready = [n for n in graph.nodes() if n.state == NodeState.READY]
        blocked = [n for n in graph.nodes() if n.state == NodeState.BLOCKED]
        assert len(ready) == 1 and len(blocked) == 4
        kernel.shutdown()
