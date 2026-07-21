from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional

from reflection.models import (
    ProposedTask,
    ReflectionDecision,
    ReflectionRequest,
    ReflectionVerdict,
)
from reflection.parser import ReflectionParser
from reflection.prompts import DefaultReflectionPrompt, ReflectionPrompt
from services.llm import LLMClient

logger = logging.getLogger("agentos.reflection")


class Reflector(ABC):
    """
    The reflection strategy contract: judge one completed task, return a decision.

    Pure by construction — a reflector reads a :class:`ReflectionRequest` and
    returns a :class:`ReflectionDecision`. It performs no side effects; the
    :class:`~reflection.coordinator.ReflectionCoordinator` applies the verdict to
    the live graph. This is the single seam where "how do we judge work and
    decide to replan" plugs in.
    """

    @abstractmethod
    def reflect(self, request: ReflectionRequest) -> ReflectionDecision:
        """Judge *request* and return a decision (never raises)."""


class HeuristicReflector(Reflector):
    """
    Deterministic, LLM-free reflector — the offline / CI strategy.

    Accepts any substantive output; if the output is empty or trivially short
    (the quality bar inherited from the legacy ``ReflectionAgent``), it proposes
    a single corrective follow-up reusing the task's own capabilities. Useful as
    a fallback and to prove the :class:`Reflector` port has more than one
    implementation.
    """

    def __init__(self, *, min_output_length: int = 10) -> None:
        self._min_len = min_output_length

    def reflect(self, request: ReflectionRequest) -> ReflectionDecision:
        if request.success and len(request.output.strip()) >= self._min_len:
            return ReflectionDecision.accept(reason="output meets the quality bar")
        return ReflectionDecision(
            verdict=ReflectionVerdict.REPLAN,
            reason="output empty or below the quality bar",
            new_tasks=[
                ProposedTask(
                    description=f"Improve and complete: {request.description}",
                    capabilities=list(request.allowed_capabilities),
                )
            ],
        )


class LLMReflector(Reflector):
    """
    LLM-backed reflector — the production strategy.

    Renders a rubric prompt, asks the model to grade the output and (only when
    warranted) propose follow-up steps, and parses the JSON verdict. It is
    **fail-open**: any backend or parse failure degrades to ``ACCEPT`` so a flaky
    model can never stall or crash a run. All judgement lives in the model — no
    hard-coded rules.
    """

    def __init__(
        self,
        llm: LLMClient,
        *,
        prompt: Optional[ReflectionPrompt] = None,
        parser: Optional[ReflectionParser] = None,
    ) -> None:
        self._llm = llm
        self._prompt = prompt or DefaultReflectionPrompt()
        self._parser = parser or ReflectionParser()

    def reflect(self, request: ReflectionRequest) -> ReflectionDecision:
        rendered = self._prompt.render(request)
        try:
            raw = self._llm.complete(rendered)
        except Exception as exc:  # noqa: BLE001 — advisory: never break the run
            logger.warning("Reflection LLM call failed (%s); accepting.", exc)
            return ReflectionDecision.accept(reason="reflection backend unavailable")
        decision = self._parser.parse(raw)  # parser is itself fail-open
        logger.info(
            "Reflection on %s → %s (%s follow-up(s)).",
            request.task_id, decision.verdict.value, len(decision.new_tasks),
        )
        return decision
