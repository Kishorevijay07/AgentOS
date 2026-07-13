"""
planning — the AgentOS Planner subsystem.

Turns a high-level :class:`Goal` into an ordered, validated :class:`Plan`, then
into runtime :class:`~models.task.Task` objects seeded onto the task queue. It is
the *front door* of the runtime: it runs before the Kernel's tick loop and only
ever produces tasks.

Boundary contract
-----------------
The planner **never** executes tasks, calls workers, knows which workers exist,
or references the scheduler. Its only side-effecting dependency is the
``AbstractTaskQueue`` (plus an optional ``AbstractEventBus`` for observability).

Quick start
-----------
>>> from planning import PlanningService, TemplatePlanner
>>> from task_queue import TaskQueue
>>> service = PlanningService(TemplatePlanner(), TaskQueue())
>>> ids = service.create_and_enqueue("Build a REST API for a blog")

To use a real LLM, inject an :class:`~services.llm.LLMClient` into
:class:`LLMPlanner` instead of using :class:`TemplatePlanner`.
"""

from planning.errors import (
    EmptyPlanError,
    PlanGenerationError,
    PlanningError,
    PlanParseError,
    PlanRejectedError,
    PlanValidationError,
)
from planning.models import Goal, Plan, PlanStep
from planning.parser import JSONPlanParser, PlanOutputParser
from planning.planner import LLMPlanner, Planner, StepTemplate, TemplatePlanner
from planning.prompts import DefaultPlanningPrompt, PromptTemplate
from planning.service import ApprovalGate, PlanningService
from planning.task_factory import TaskFactory
from planning.validation import PlanValidator

__all__ = [
    # models
    "Goal",
    "Plan",
    "PlanStep",
    # planner strategies
    "Planner",
    "LLMPlanner",
    "TemplatePlanner",
    "StepTemplate",
    # prompt / parse / validate / translate
    "PromptTemplate",
    "DefaultPlanningPrompt",
    "PlanOutputParser",
    "JSONPlanParser",
    "PlanValidator",
    "TaskFactory",
    # service + approval
    "PlanningService",
    "ApprovalGate",
    # errors
    "PlanningError",
    "PlanGenerationError",
    "PlanParseError",
    "PlanValidationError",
    "EmptyPlanError",
    "PlanRejectedError",
]
