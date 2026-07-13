from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from models.enums import Priority


class Goal(BaseModel):
    """
    A high-level objective handed to the Planner by a user or another agent.

    The Planner's job is to turn this into an ordered :class:`Plan`. A ``Goal``
    is intentionally thin — it carries the natural-language objective plus a few
    knobs that constrain planning, and never any domain-specific structure.

    Attributes
    ----------
    description:
        The natural-language objective, e.g. ``"Build a REST API for a blog"``.
    context:
        Optional extra context (constraints, prior results, memory) the planner
        may fold into its prompt. Kept free-form so memory/MCP integrations can
        inject whatever they need without a schema change.
    max_steps:
        Upper bound on plan size — a guard-rail against runaway decompositions.
    metadata:
        Arbitrary caller-supplied tags (request id, tenant, priority hints).
    """

    description: str = Field(min_length=1, description="The natural-language objective.")
    context: Optional[str] = Field(default=None, description="Optional planning context.")
    max_steps: int = Field(default=20, ge=1, le=200, description="Maximum plan size.")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PlanStep(BaseModel):
    """
    One node in a :class:`Plan` — a single unit of work to be executed later.

    A step is **capability-tagged, not worker-bound**: it declares *what kind*
    of work it is (``["code"]``, ``["research"]``) so the Scheduler can route
    it, but it never names a worker. Dependencies are expressed against other
    steps' ``order`` values, making a plan a DAG rather than a flat list — which
    is what enables future parallel and hierarchical planning.

    Attributes
    ----------
    order:
        1-based position / identity of the step within its plan. Also the key
        other steps reference in ``depends_on``.
    description:
        What this step accomplishes, e.g. ``"Create database schema"``.
    capabilities:
        Capability labels required to execute the step. May be empty (a generic
        step any worker can take).
    depends_on:
        ``order`` values of steps that must complete first. Must reference
        **earlier** steps only (enforced by the validator) — no cycles.
    priority:
        Scheduling priority for the resulting task. Defaults to ``MEDIUM``.
    """

    order: int = Field(ge=1, description="1-based step position and identity.")
    description: str = Field(min_length=1, description="What the step accomplishes.")
    capabilities: List[str] = Field(default_factory=list)
    depends_on: List[int] = Field(default_factory=list)
    priority: Priority = Priority.MEDIUM


class Plan(BaseModel):
    """
    An ordered, validated decomposition of a :class:`Goal` into :class:`PlanStep`\\ s.

    The ``Plan`` is a pure data structure — it holds no behaviour and performs
    no side effects. Structural policy (non-empty, acyclic dependencies, size
    limits) is enforced by :class:`~planning.validation.PlanValidator`, keeping
    the model reusable and the validation rules swappable.

    Attributes
    ----------
    goal:
        The original goal description this plan was produced from.
    steps:
        The ordered steps. May be empty at construction time — the validator is
        responsible for rejecting empty plans, so parse and validation failures
        stay in distinct error domains.
    """

    goal: str = Field(min_length=1)
    steps: List[PlanStep] = Field(default_factory=list)

    def ordered_steps(self) -> List[PlanStep]:
        """Return the steps sorted by ``order`` (defensive copy)."""
        return sorted(self.steps, key=lambda s: s.order)

    def __len__(self) -> int:
        return len(self.steps)
