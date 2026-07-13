"""
Full-stack integration — Planner → Task Graph → Scheduler → Runtime → Workers.

The whole AgentOS pipeline: a goal is planned, compiled into a DAG, and executed
by capability-matched workers managed by the runtime, with retries and metrics.
"""
from __future__ import annotations

from agents.coding import CodingAgent
from agents.documentation import DocumentationAgent
from agents.research import ResearchAgent
from agents.testing import TestingAgent
from events.bus import InMemoryEventBus
from events.event_type import EventType
from planning import TemplatePlanner
from planning.models import Goal
from result_store import ResultStore
from runtime import DefaultWorkerRuntime
from scheduling import ExecutionScheduler
from task_graph import PlanGraphBuilder


class TestFullPipeline:
    def test_goal_to_completed_dag(self):
        # 1. Plan.
        plan = TemplatePlanner().plan(Goal(description="Build a REST API for a blog"))
        # 2. Compile to DAG.
        graph = PlanGraphBuilder().build(plan)
        # 3. Stand up the worker runtime.
        bus = InMemoryEventBus()
        store = ResultStore()
        runtime = DefaultWorkerRuntime(event_bus=bus)
        worker_ids = [
            runtime.register_worker(a)
            for a in (ResearchAgent(), CodingAgent(), TestingAgent(), DocumentationAgent())
        ]
        # 4. Schedule execution.
        scheduler = ExecutionScheduler(graph, runtime, event_bus=bus, result_store=store)
        scheduler.run_until_idle()

        # Every task in the DAG ran to completion.
        assert len(graph.completed_tasks()) == 5
        assert graph.has_active_work() is False

        # The runtime recorded metrics; total executions == number of tasks.
        total = sum(runtime.worker_metrics(w).tasks_executed for w in worker_ids)
        assert total == 5

        # Execution traces were captured, one per task.
        assert len(store.all()) == 5
        assert all(r.success for r in store.all())

        # Lifecycle events flowed on the bus.
        types = {e.type for e in bus.history()}
        assert EventType.TASK_COMPLETED in types
        assert EventType.AGENT_ONLINE in types

        runtime.shutdown()

    def test_capability_routing_uses_correct_worker(self):
        # Only a research-capable worker is available; a code task cannot run.
        plan = TemplatePlanner().plan(Goal(description="x"))
        graph = PlanGraphBuilder().build(plan)

        runtime = DefaultWorkerRuntime()
        runtime.register_worker(ResearchAgent())  # research only
        scheduler = ExecutionScheduler(graph, runtime)
        scheduler.run_until_idle()

        # The two research steps complete; the code/test/doc steps cannot be
        # placed and remain blocked/ready — never mis-routed.
        completed = [n.description for n in graph.completed_tasks()]
        assert any("Analyze" in d for d in completed)
        assert len(graph.completed_tasks()) < 5
        runtime.shutdown()
