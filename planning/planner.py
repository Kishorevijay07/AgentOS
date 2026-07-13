from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Sequence

from models.enums import Priority
from planning.errors import PlanGenerationError, PlanningError
from planning.models import Goal, Plan, PlanStep
from planning.parser import JSONPlanParser, PlanOutputParser
from planning.prompts import DefaultPlanningPrompt, PromptTemplate
from services.llm import LLMClient

logger = logging.getLogger("agentos.planning")


class Planner(ABC):
    """
    The planning strategy contract: turn a :class:`Goal` into a :class:`Plan`.

    This is the single seam where "how do we decompose a goal" plugs in. Every
    consumer (notably :class:`~planning.service.PlanningService`) depends on
    *this type*, never on a concrete planner — so an LLM planner, a template
    planner, or a future hierarchical/multi-agent planner are interchangeable.

    A ``Planner`` is **pure**: it generates a plan and returns it. It performs no
    validation (that is the validator's job) and no side effects — it never
    touches the queue, scheduler, or workers.
    """

    @abstractmethod
    def plan(self, goal: Goal) -> Plan:
        """
        Produce an ordered :class:`Plan` for *goal*.

        Raises
        ------
        PlanningError
            If a plan could not be produced (generation or parse failure).
        """


class LLMPlanner(Planner):
    """
    LLM-backed planner — the production strategy.

    All domain understanding lives here, and *only* here, delegated entirely to
    the language model. There is no ``if "api" in goal`` anywhere: the planner
    renders a prompt, asks the model, and parses the answer. That is what lets a
    single implementation handle coding, research, analysis, long, or short
    goals without domain-specific branches.

    Collaborators are injected (Dependency Inversion), each behind its own port:

    * ``llm`` — a :class:`~services.llm.LLMClient` (Anthropic in prod, a fake in
      tests);
    * ``prompt`` — a :class:`~planning.prompts.PromptTemplate`;
    * ``parser`` — a :class:`~planning.parser.PlanOutputParser`.
    """

    def __init__(
        self,
        llm: LLMClient,
        *,
        prompt: PromptTemplate | None = None,
        parser: PlanOutputParser | None = None,
    ) -> None:
        self._llm = llm
        self._prompt = prompt or DefaultPlanningPrompt()
        self._parser = parser or JSONPlanParser()

    def plan(self, goal: Goal) -> Plan:
        rendered = self._prompt.render(goal)
        logger.debug("LLMPlanner rendered prompt (%d chars).", len(rendered))

        try:
            raw = self._llm.complete(rendered)
        except PlanningError:
            raise
        except Exception as exc:  # noqa: BLE001 — normalise any backend failure
            raise PlanGenerationError(f"LLM completion failed: {exc}") from exc

        plan = self._parser.parse(raw, goal)
        logger.info("LLMPlanner produced a plan with %d step(s).", len(plan))
        return plan


@dataclass(frozen=True)
class StepTemplate:
    """A single templated step for :class:`TemplatePlanner` (``{goal}`` is filled in)."""

    description: str
    capabilities: Sequence[str] = ()
    depends_on: Sequence[int] = ()
    priority: Priority = Priority.MEDIUM


# A generic software-delivery lifecycle. It is a *strategy default*, not domain
# logic embedded in the planner — swap the templates to change it entirely.
_DEFAULT_TEMPLATES: tuple[StepTemplate, ...] = (
    StepTemplate("Analyze and research requirements for: {goal}", ["research"]),
    StepTemplate("Design the solution for: {goal}", ["research"], depends_on=(1,)),
    StepTemplate("Implement the solution for: {goal}", ["code"], depends_on=(2,)),
    StepTemplate("Write tests for: {goal}", ["test"], depends_on=(3,)),
    StepTemplate("Document the implementation of: {goal}", ["document"], depends_on=(4,)),
)


class TemplatePlanner(Planner):
    """
    Deterministic, offline planner built from injected step templates.

    It requires no LLM, so it is ideal for tests, CI, and air-gapped runs, and it
    demonstrates that the :class:`Planner` port genuinely has more than one
    implementation. The default templates describe a generic delivery lifecycle;
    pass your own ``templates`` to change the behaviour without subclassing.
    """

    def __init__(self, templates: Sequence[StepTemplate] | None = None) -> None:
        self._templates = tuple(templates) if templates is not None else _DEFAULT_TEMPLATES

    def plan(self, goal: Goal) -> Plan:
        steps: List[PlanStep] = []
        for index, template in enumerate(self._templates, start=1):
            steps.append(
                PlanStep(
                    order=index,
                    description=template.description.format(goal=goal.description),
                    capabilities=list(template.capabilities),
                    depends_on=list(template.depends_on),
                    priority=template.priority,
                )
            )
        logger.info("TemplatePlanner produced a plan with %d step(s).", len(steps))
        return Plan(goal=goal.description, steps=steps)
