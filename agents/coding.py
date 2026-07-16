from __future__ import annotations

from typing import Any, List, Optional

from agents.base import BaseAgent
from agents.worker import WorkerMixin
from models.task import Task
from services.llm import LLMClient


class CodingAgent(WorkerMixin, BaseAgent):
    """
    Handles code-generation tasks such as implementing features,
    writing boilerplate, and refactoring existing code.

    Intelligence is injected, not hardcoded: pass any
    :class:`~services.llm.LLMClient` (e.g. ``OpenRouterLLMClient.from_env()``)
    and ``execute`` delegates the actual coding to the model. With no client the
    agent falls back to a deterministic placeholder — which keeps offline runs,
    CI, and the existing test suite working unchanged.
    """

    capabilities: List[str] = ["code", "implement", "refactor", "debug"]

    _PROMPT = (
        "You are a senior software engineer inside an autonomous agent runtime. "
        "Complete the following coding task. Return only the deliverable "
        "(code plus brief inline comments); no preamble.\n\nTASK:\n{task}"
    )

    def __init__(self, llm: Optional[LLMClient] = None) -> None:
        self._llm = llm

    def execute(self, task: Task) -> Any:
        """
        Execute a coding task — via the injected LLM when configured,
        otherwise a deterministic placeholder.
        """
        if self._llm is not None:
            return self._llm.complete(self._PROMPT.format(task=task.description))
        return f"[CodingAgent] Executed: {task.description}"
