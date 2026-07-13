"""Unit tests for PlanGraphBuilder and MermaidVisualizer."""
from __future__ import annotations

from planning import TemplatePlanner
from planning.models import Goal, Plan, PlanStep
from task_graph import InMemoryTaskGraph, MermaidVisualizer, PlanGraphBuilder
from task_graph.state import NodeState


class TestPlanGraphBuilder:
    def test_builds_graph_from_template_plan(self):
        plan = TemplatePlanner().plan(Goal(description="Build a REST API"))
        graph = PlanGraphBuilder().build(plan)

        assert len(graph.nodes()) == 5
        # The linear lifecycle => exactly one source is ready.
        ready = graph.ready_tasks()
        assert len(ready) == 1
        assert "Analyze" in ready[0].description

    def test_dependency_edges_are_wired(self):
        # step 2 depends on step 1
        plan = Plan(
            goal="g",
            steps=[
                PlanStep(order=1, description="first", capabilities=["code"]),
                PlanStep(order=2, description="second", depends_on=[1]),
            ],
        )
        graph = PlanGraphBuilder().build(plan)
        nodes = {n.description: n for n in graph.nodes()}
        assert nodes["second"].state == NodeState.BLOCKED
        assert nodes["first"].state == NodeState.READY
        # first -> second edge exists
        assert nodes["second"].dependencies == [nodes["first"].task_id]

    def test_build_into_injected_graph(self):
        plan = TemplatePlanner().plan(Goal(description="x"))
        graph = InMemoryTaskGraph()
        returned = PlanGraphBuilder().build(plan, graph=graph)
        assert returned is graph


class TestVisualizer:
    def test_mermaid_contains_nodes_and_edges(self):
        plan = Plan(
            goal="g",
            steps=[
                PlanStep(order=1, description="Alpha"),
                PlanStep(order=2, description="Beta", depends_on=[1]),
            ],
        )
        graph = PlanGraphBuilder().build(plan, graph=InMemoryTaskGraph(visualizer=MermaidVisualizer()))
        diagram = graph.visualize()

        assert diagram.startswith("graph TD")
        assert "Alpha" in diagram
        assert "Beta" in diagram
        assert "-->" in diagram  # at least one edge
        assert "classDef" in diagram
