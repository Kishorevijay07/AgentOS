from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any, List

from planning.errors import PlanParseError
from planning.models import Goal, Plan, PlanStep


class PlanOutputParser(ABC):
    """
    Contract for turning raw planner output into a structured :class:`Plan`.

    Parsing is its own failure domain: LLM output is untrusted text that may be
    malformed, fenced, or wrapped in prose. Isolating it behind this interface
    (a) keeps that messiness out of the planner and service, and (b) lets a
    deployment swap parsing strategies (JSON, YAML, numbered list) without
    touching anything else.
    """

    @abstractmethod
    def parse(self, raw: str, goal: Goal) -> Plan:
        """
        Parse *raw* into a :class:`Plan` for *goal*.

        Raises
        ------
        PlanParseError
            If *raw* cannot be interpreted as a plan.
        """


# Matches a ```json … ``` or ``` … ``` fenced block; group 1 is the inner body.
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


class JSONPlanParser(PlanOutputParser):
    """
    Parse a JSON array (or ``{"steps": [...]}``) of step objects into a Plan.

    Tolerant of the two things real models do most often: wrapping the JSON in
    Markdown code fences, and surrounding it with a sentence of prose. It never
    guesses at *content* — only at *envelope* — so a genuinely malformed plan
    still fails loudly with :class:`PlanParseError`.
    """

    def parse(self, raw: str, goal: Goal) -> Plan:
        payload = self._extract_json(raw)
        steps_data = self._coerce_to_step_list(payload)

        steps: List[PlanStep] = []
        for index, item in enumerate(steps_data, start=1):
            steps.append(self._build_step(item, index))

        return Plan(goal=goal.description, steps=steps)

    # ------------------------------------------------------------------ #
    #  Internals
    # ------------------------------------------------------------------ #

    def _extract_json(self, raw: str) -> Any:
        """Strip optional code fences / prose and ``json.loads`` the remainder."""
        if raw is None or not raw.strip():
            raise PlanParseError("Planner returned empty output.")

        candidate = raw.strip()
        fenced = _FENCE_RE.search(candidate)
        if fenced:
            candidate = fenced.group(1).strip()
        else:
            # Fall back to the outermost JSON array/object in the text.
            candidate = self._slice_outermost_json(candidate)

        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError) as exc:
            raise PlanParseError(f"Planner output was not valid JSON: {exc}") from exc

    @staticmethod
    def _slice_outermost_json(text: str) -> str:
        """Return the substring from the first ``[``/``{`` to its matching close."""
        start_candidates = [i for i in (text.find("["), text.find("{")) if i != -1]
        if not start_candidates:
            return text  # let json.loads produce a precise error
        start = min(start_candidates)
        open_ch = text[start]
        close_ch = "]" if open_ch == "[" else "}"
        end = text.rfind(close_ch)
        if end <= start:
            return text
        return text[start : end + 1]

    @staticmethod
    def _coerce_to_step_list(payload: Any) -> List[dict]:
        """Accept either a bare array or ``{"steps": [...]}``."""
        if isinstance(payload, dict):
            payload = payload.get("steps", payload.get("plan"))
        if not isinstance(payload, list):
            raise PlanParseError(
                "Expected a JSON array of steps (or an object with a 'steps' array)."
            )
        return payload

    @staticmethod
    def _build_step(item: Any, index: int) -> PlanStep:
        """Build one :class:`PlanStep`, defaulting order to positional index."""
        if isinstance(item, str):
            # Model returned a plain list of strings — accept it gracefully.
            return PlanStep(order=index, description=item)
        if not isinstance(item, dict):
            raise PlanParseError(f"Step {index} is not an object or string: {item!r}.")

        description = item.get("description") or item.get("task") or item.get("title")
        if not description or not str(description).strip():
            raise PlanParseError(f"Step {index} is missing a non-empty description.")

        try:
            return PlanStep(
                order=int(item.get("order", index)),
                description=str(description).strip(),
                capabilities=list(item.get("capabilities", []) or []),
                depends_on=[int(d) for d in item.get("depends_on", []) or []],
            )
        except (TypeError, ValueError) as exc:
            raise PlanParseError(f"Step {index} has invalid fields: {exc}") from exc
