from __future__ import annotations

from typing import Protocol, runtime_checkable

from planning.models import Goal


@runtime_checkable
class PromptTemplate(Protocol):
    """
    Port for rendering a :class:`Goal` into an LLM instruction string.

    Prompts are the highest-churn artifact in any LLM system. Isolating them
    behind a port means prompt iteration — few-shot examples, hierarchical or
    parallel-planning instructions — never touches planner logic. Swap the
    template, keep everything else.
    """

    def render(self, goal: Goal) -> str:
        """Return the fully-rendered prompt for *goal*."""
        ...


class DefaultPlanningPrompt:
    """
    The default zero-shot planning prompt.

    It instructs the model to return a **strict JSON array** of steps, each with
    ``description``, ``capabilities``, and ``depends_on`` — the exact shape
    :class:`~planning.parser.JSONPlanParser` consumes. It is deliberately
    domain-neutral: it tells the model *how to structure* a plan, never *what*
    the plan should contain, so it works for coding, research, analysis, or
    generic goals without change.

    ``capability_hint`` lets a deployment advertise the capability vocabulary its
    worker pool understands (e.g. ``["code", "research", "test", "document"]``)
    without hardcoding it here.
    """

    _INSTRUCTIONS = (
        "You are a planning module in an autonomous agent runtime. Decompose the "
        "GOAL below into an ordered list of concrete, independently-executable "
        "steps. Do not execute anything; only plan.\n\n"
        "Respond with STRICT JSON: an array of objects, each with:\n"
        '  - "description": string, an imperative instruction for one step\n'
        '  - "capabilities": array of lowercase capability tags for the step\n'
        '  - "depends_on": array of 1-based indexes of earlier steps that must '
        "finish first (empty if none)\n\n"
        "Rules: keep steps atomic; order them so dependencies come first; use at "
        "most {max_steps} steps; output ONLY the JSON array, no prose, no code fences."
    )

    def __init__(self, capability_hint: list[str] | None = None) -> None:
        self._capability_hint = capability_hint or []

    def render(self, goal: Goal) -> str:
        parts = [self._INSTRUCTIONS.format(max_steps=goal.max_steps)]
        if self._capability_hint:
            parts.append(
                "Available capability tags: " + ", ".join(self._capability_hint) + "."
            )
        if goal.context:
            parts.append(f"CONTEXT:\n{goal.context}")
        parts.append(f"GOAL:\n{goal.description}")
        return "\n\n".join(parts)
