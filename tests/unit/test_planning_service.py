"""Unit tests for PlanningService — the orchestration + boundary contract."""
from __future__ import annotations

from typing import List

import pytest

from events.bus import InMemoryEventBus
from events.event_type import EventType
from models.enums import Status
from planning.errors import PlanRejectedError, PlanValidationError
from planning.models import Goal, Plan, PlanStep
from planning.planner import Planner, StepTemplate, TemplatePlanner
from planning.service import PlanningService
from task_queue.task_queue import TaskQueue


class _FixedPlanner(Planner):
    """Returns a caller-supplied plan verbatim — lets tests control the plan."""

    def __init__(self, plan: Plan) -> None:
        self._plan = plan

    def plan(self, goal: Goal) -> Plan:
        return self._plan


class TestPlanOnly:
    def test_plan_returns_validated_plan_without_enqueue(self):
        queue = TaskQueue()
        service = PlanningService(TemplatePlanner(), queue)
        plan = service.plan("Build a blog API")
        assert len(plan) == 5
        assert len(queue) == 0  # nothing enqueued

    def test_string_goal_is_coerced(self):
        service = PlanningService(TemplatePlanner(), TaskQueue())
        assert isinstance(service.plan("hello"), Plan)


class TestCreateAndEnqueue:
    def test_enqueues_tasks_and_returns_ids(self):
        queue = TaskQueue()
        service = PlanningService(TemplatePlanner(), queue)

        ids = service.create_and_enqueue("Build a blog API")

        assert len(ids) == 5
        pending = queue.pending_tasks()
        assert len(pending) == 5
        assert {t.id for t in pending} == set(ids)
        assert all(t.status == Status.PENDING for t in pending)

    def test_dependencies_are_wired_to_task_uuids(self):
        queue = TaskQueue()
        # Two steps; step 2 depends on step 1.
        planner = _FixedPlanner(
            Plan(
                goal="g",
                steps=[
                    PlanStep(order=1, description="first", capabilities=["code"]),
                    PlanStep(order=2, description="second", depends_on=[1]),
                ],
            )
        )
        service = PlanningService(planner, queue)
        ids = service.create_and_enqueue("g")

        by_id = {t.id: t for t in queue.pending_tasks()}
        second = by_id[ids[1]]
        assert second.dependencies == [ids[0]]

    def test_publishes_task_created_per_task(self):
        bus = InMemoryEventBus()
        service = PlanningService(TemplatePlanner(), TaskQueue(), event_bus=bus)
        service.create_and_enqueue("Build a blog API")

        created = [e for e in bus.history() if e.type == EventType.TASK_CREATED]
        assert len(created) == 5
        assert all(e.source == "Planner" for e in created)

    def test_validation_error_prevents_enqueue(self):
        queue = TaskQueue()
        # max_steps=2 but the template produces 5 → validation must reject.
        service = PlanningService(TemplatePlanner(), queue)
        with pytest.raises(PlanValidationError):
            service.create_and_enqueue(Goal(description="x", max_steps=2))
        assert len(queue) == 0


class TestApprovalGate:
    def test_rejected_plan_enqueues_nothing(self):
        queue = TaskQueue()

        class _Deny:
            def approve(self, plan: Plan) -> bool:
                return False

        service = PlanningService(TemplatePlanner(), queue, approval_gate=_Deny())
        with pytest.raises(PlanRejectedError):
            service.create_and_enqueue("Build a blog API")
        assert len(queue) == 0

    def test_approved_plan_enqueues(self):
        queue = TaskQueue()

        class _Allow:
            def approve(self, plan: Plan) -> bool:
                return True

        service = PlanningService(TemplatePlanner(), queue, approval_gate=_Allow())
        ids = service.create_and_enqueue("Build a blog API")
        assert len(ids) == 5


class TestBoundaryContract:
    def test_planning_package_never_imports_scheduler_or_workers(self):
        """
        Structural guarantee: no module in the planning package imports the
        scheduler, the kernel/dispatcher, or the agents/worker layer. The planner
        only knows about the task queue and events.
        """
        import ast
        import pathlib

        import planning

        forbidden_roots = {"scheduler", "kernel", "agents"}
        package_dir = pathlib.Path(planning.__file__).parent
        offenders: list[str] = []

        for path in package_dir.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules = [node.module]
                for module in modules:
                    if module.split(".")[0] in forbidden_roots:
                        offenders.append(f"{path.name}: {module}")

        assert offenders == [], f"planning imported forbidden modules: {offenders}"
