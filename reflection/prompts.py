from __future__ import annotations

from typing import Protocol, runtime_checkable

from reflection.models import ReflectionRequest


@runtime_checkable
class ReflectionPrompt(Protocol):
    """
    Port for rendering a :class:`ReflectionRequest` into an LLM instruction.

    Reflection is planning's mirror image, so — as with
    :class:`~planning.prompts.PromptTemplate` — the prompt is isolated behind a
    port. Iterate on the rubric without touching reflection logic.
    """

    def render(self, request: ReflectionRequest) -> str:
        """Return the fully-rendered reflection prompt."""
        ...


class DefaultReflectionPrompt:
    """
    Default zero-shot reflection rubric.

    It asks the model to grade a completed task's output and return **strict
    JSON** — a verdict plus, when the work is insufficient, concrete follow-up
    steps. It is domain-neutral (it grades *fitness for the task*, never a
    hard-coded checklist) and, like :class:`~planning.prompts.DefaultPlanningPrompt`,
    accepts a capability hint so any proposed follow-up is actually schedulable.
    """

    _INSTRUCTIONS = (
        "You are a reflection module in an autonomous agent runtime. A worker "
        "just completed a task. Judge whether the OUTPUT adequately satisfies the "
        "TASK (and the overall GOAL, if given). Do not redo the work yourself.\n\n"
        "Respond with STRICT JSON, an object with:\n"
        '  - "verdict": "accept" if the output is good enough, else "replan"\n'
        '  - "reason": one short sentence\n'
        '  - "new_tasks": array (empty when accepting) of follow-up steps, each '
        'an object with "description" (imperative) and "capabilities" (array of '
        "tags)\n\n"
        "Only propose follow-ups that are genuinely necessary. Output ONLY the "
        "JSON object — no prose, no code fences."
    )

    def __init__(self, capability_hint: list[str] | None = None) -> None:
        self._capability_hint = capability_hint or []

    def render(self, request: ReflectionRequest) -> str:
        parts = [self._INSTRUCTIONS]
        # Prefer the per-request capability vocabulary (set by the coordinator);
        # fall back to any hint fixed at construction.
        capabilities = request.allowed_capabilities or self._capability_hint
        if capabilities:
            parts.append(
                "Proposed follow-up tasks MUST tag \"capabilities\" using ONLY "
                "these (never invent new ones): "
                + ", ".join(capabilities)
                + "."
            )
        if request.goal:
            parts.append(f"GOAL:\n{request.goal}")
        parts.append(f"TASK:\n{request.description}")
        status = "succeeded" if request.success else f"failed ({request.error})"
        parts.append(f"OUTCOME: {status}")
        parts.append(f"OUTPUT:\n{request.output}")
        return "\n\n".join(parts)
