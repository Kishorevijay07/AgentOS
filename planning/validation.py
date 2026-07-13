from __future__ import annotations

from planning.errors import EmptyPlanError, PlanValidationError
from planning.models import Goal, Plan


class PlanValidator:
    """
    Enforces the structural invariants a :class:`Plan` must satisfy before it is
    allowed to become real work.

    Validation is deliberately separate from parsing and generation: a plan can
    be perfectly parseable JSON yet still be nonsense (empty, self-referential,
    too large, forward-referencing dependencies). Concentrating those rules here
    means the policy can be tightened or subclassed without disturbing the
    planner or the parser, and every failure surfaces as a
    :class:`PlanValidationError` the service can handle uniformly.

    Rules enforced
    --------------
    1. The plan has at least one step (else :class:`EmptyPlanError`).
    2. The plan has no more than ``goal.max_steps`` steps.
    3. Step ``order`` values are unique.
    4. Every ``depends_on`` entry references an **existing, earlier** step —
       which rules out self-dependencies, forward references, and (because edges
       only ever point backwards) dependency cycles.
    """

    def validate(self, plan: Plan, goal: Goal) -> None:
        """Raise :class:`PlanValidationError` if *plan* is not executable."""
        if len(plan) == 0:
            raise EmptyPlanError(f"Planner produced no steps for goal: {goal.description!r}.")

        if len(plan) > goal.max_steps:
            raise PlanValidationError(
                f"Plan has {len(plan)} steps, exceeding max_steps={goal.max_steps}."
            )

        orders = [s.order for s in plan.steps]
        if len(set(orders)) != len(orders):
            raise PlanValidationError(f"Duplicate step orders in plan: {orders}.")

        seen: set[int] = set()
        for step in plan.ordered_steps():
            for dep in step.depends_on:
                if dep == step.order:
                    raise PlanValidationError(
                        f"Step {step.order} depends on itself."
                    )
                if dep not in seen:
                    raise PlanValidationError(
                        f"Step {step.order} depends on step {dep}, which is not a "
                        "prior step (forward reference or missing step)."
                    )
            seen.add(step.order)
