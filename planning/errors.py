from __future__ import annotations


class PlanningError(Exception):
    """
    Base class for every error raised by the planning subsystem.

    Callers that only care "did planning succeed?" can catch this single type;
    callers that need to react differently to *why* it failed can catch the
    specific subclasses below. Keeping a dedicated hierarchy (rather than
    leaking ``ValueError``/``json.JSONDecodeError``) means the planning
    subsystem exposes a stable failure contract independent of its internals.
    """


class PlanGenerationError(PlanningError):
    """The underlying strategy (e.g. the LLM call) failed to produce output."""


class PlanParseError(PlanningError):
    """Raw planner output could not be parsed into a structured :class:`Plan`."""


class PlanValidationError(PlanningError):
    """A parsed plan violated a structural invariant (bad deps, too large, …)."""


class EmptyPlanError(PlanValidationError):
    """A plan contained no steps — a special, common case of invalid plan."""


class PlanRejectedError(PlanningError):
    """An approval gate declined the plan; no tasks were enqueued."""
