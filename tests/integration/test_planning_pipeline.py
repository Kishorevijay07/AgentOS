"""
Integration test — Planner → TaskQueue → Kernel runtime.

Proves the end-to-end story the Planner exists for: a natural-language goal is
decomposed and seeded onto the Kernel's queue by the PlanningService, then the
existing runtime (Dispatcher + workers) executes those tasks — with the Planner
never touching the scheduler or workers itself.
"""
from __future__ import annotations

from events.event_type import EventType
from kernel import build_kernel
from planning import LLMPlanner, PlanningService, TemplatePlanner
from services.llm import StaticLLMClient

from agents.coding import CodingAgent
from agents.documentation import DocumentationAgent
from agents.research import ResearchAgent
from agents.testing import TestingAgent


def _kernel_with_workers():
    kernel = build_kernel().boot()
    for agent in (CodingAgent(), ResearchAgent(), TestingAgent(), DocumentationAgent()):
        kernel.register_agent(agent)
    return kernel


class TestTemplatePlannerPipeline:
    def test_goal_becomes_executed_tasks(self):
        kernel = _kernel_with_workers()
        service = PlanningService(
            TemplatePlanner(), kernel.task_queue, event_bus=kernel.bus
        )

        ids = service.create_and_enqueue("Build a REST API for a blog")
        assert len(ids) == 5

        results = kernel.run_until_idle()

        # Every planned task executed successfully.
        assert len(results) == 5
        assert all(r.success for r in results)
        # Each id has a closed, successful execution record.
        for task_id in ids:
            record = kernel.store.get(task_id)
            assert record is not None and record.success is True

    def test_planner_publishes_task_created_from_planner_source(self):
        kernel = _kernel_with_workers()
        service = PlanningService(
            TemplatePlanner(), kernel.task_queue, event_bus=kernel.bus
        )
        service.create_and_enqueue("Analyze sales data")

        planner_events = [
            e
            for e in kernel.bus.history()
            if e.type == EventType.TASK_CREATED and e.source == "Planner"
        ]
        assert len(planner_events) == 5


class TestLLMPlannerPipeline:
    def test_llm_plan_seeds_and_runs(self):
        # A deterministic stand-in for a real LLM response.
        canned = (
            '[{"description": "Design the API", "capabilities": ["code"]},'
            ' {"description": "Implement endpoints", "capabilities": ["code"], "depends_on": [1]},'
            ' {"description": "Write tests", "capabilities": ["test"], "depends_on": [2]}]'
        )
        kernel = _kernel_with_workers()
        service = PlanningService(
            LLMPlanner(StaticLLMClient(canned)), kernel.task_queue, event_bus=kernel.bus
        )

        ids = service.create_and_enqueue("Build a REST API for a blog")
        results = kernel.run_until_idle()

        assert len(ids) == 3
        assert len(results) == 3
        assert all(r.success for r in results)
