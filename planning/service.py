from __future__ import annotations

import logging
from typing import List, Optional, Protocol, Union, runtime_checkable
from uuid import UUID

from events.bus import AbstractEventBus
from events.event import Event
from events.event_type import EventType
from models.task import Task
from planning.errors import PlanRejectedError
from planning.models import Goal, Plan
from planning.planner import Planner
from planning.task_factory import TaskFactory
from planning.validation import PlanValidator
from task_queue.task_queue import AbstractTaskQueue


@runtime_checkable
class ApprovalGate(Protocol):
    """
    Optional human-in-the-loop gate, invoked with a validated plan before any
    task is enqueued.

    This is the concrete extension point for the "human approval" requirement:
    inject a gate and no work is created until it returns ``True``. Because it is
    a Protocol, an interactive CLI prompt, a Slack approval, or an auto-approver
    all satisfy it without a shared base class.
    """

    def approve(self, plan: Plan) -> bool:
        """Return ``True`` to allow enqueue, ``False`` to reject the plan."""
        ...


class PlanningService:
    """
    The Planner front door — orchestrates the full planning pipeline.

    ::

        goal → planner.plan() → validate → (approve) → to tasks → enqueue → ids

    It owns **sequencing and error handling only**; every actual step is
    delegated to an injected collaborator, so each stage is independently
    testable and replaceable (Single Responsibility + Dependency Inversion).

    Crucially, this class embodies the Planner's boundary contract: its only
    side-effecting dependency is the :class:`AbstractTaskQueue` (plus an optional
    :class:`AbstractEventBus` for observability). **It never imports or references
    the scheduler or any worker** — it produces tasks and stops.

    Parameters
    ----------
    planner:
        The strategy that turns a goal into a plan.
    task_queue:
        Where produced tasks are enqueued. An abstraction, so in-memory today /
        Redis tomorrow with no change here.
    validator:
        Structural plan validator. Defaults to :class:`PlanValidator`.
    task_factory:
        Plan → tasks translator. Defaults to :class:`TaskFactory`.
    event_bus:
        Optional bus; when present a ``TASK_CREATED`` event is published per task
        so the rest of the runtime observes planner-seeded work exactly as it
        observes ``Kernel.submit``.
    approval_gate:
        Optional human-approval gate consulted after validation.
    logger:
        Injectable logger; defaults to the ``agentos.planning`` logger.
    """

    def __init__(
        self,
        planner: Planner,
        task_queue: AbstractTaskQueue,
        *,
        validator: Optional[PlanValidator] = None,
        task_factory: Optional[TaskFactory] = None,
        event_bus: Optional[AbstractEventBus] = None,
        approval_gate: Optional[ApprovalGate] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._planner = planner
        self._task_queue = task_queue
        self._validator = validator or PlanValidator()
        self._task_factory = task_factory or TaskFactory()
        self._bus = event_bus
        self._approval_gate = approval_gate
        self._log = logger or logging.getLogger("agentos.planning")

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def plan(self, goal: Union[Goal, str]) -> Plan:
        """
        Generate and validate a plan **without** enqueueing anything.

        Useful for previews, human-approval UIs, and tests. Raises
        :class:`~planning.errors.PlanningError` on generation, parse, or
        validation failure.
        """
        goal = self._normalise(goal)
        self._log.info("Planning goal: %r", goal.description)
        plan = self._planner.plan(goal)
        self._validator.validate(plan, goal)
        self._log.info("Plan validated: %d step(s).", len(plan))
        return plan

    def create_and_enqueue(self, goal: Union[Goal, str]) -> List[UUID]:
        """
        Run the full pipeline and return the enqueued task IDs, in plan order.

        Raises
        ------
        planning.errors.PlanningError
            On generation, parse, or validation failure.
        planning.errors.PlanRejectedError
            If an approval gate declined the plan (nothing is enqueued).
        """
        goal = self._normalise(goal)
        plan = self.plan(goal)

        if self._approval_gate is not None and not self._approval_gate.approve(plan):
            self._log.warning("Plan for %r was rejected by approval gate.", goal.description)
            raise PlanRejectedError(f"Plan for goal {goal.description!r} was not approved.")

        tasks = self._task_factory.build(plan)
        task_ids = [self._enqueue(task) for task in tasks]
        self._log.info("Enqueued %d task(s) for goal %r.", len(task_ids), goal.description)
        return task_ids

    # ------------------------------------------------------------------ #
    #  Internals
    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalise(goal: Union[Goal, str]) -> Goal:
        """Accept a bare string for ergonomics; coerce it to a :class:`Goal`."""
        return goal if isinstance(goal, Goal) else Goal(description=goal)

    def _enqueue(self, task: Task) -> UUID:
        self._task_queue.add_task(task)
        if self._bus is not None:
            self._bus.publish(
                Event(
                    type=EventType.TASK_CREATED,
                    payload={
                        "task_id": str(task.id),
                        "description": task.description,
                        "required_capabilities": list(task.required_capabilities),
                        "dependencies": [str(d) for d in task.dependencies],
                    },
                    source="Planner",
                )
            )
        return task.id
