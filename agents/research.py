from __future__ import annotations

from typing import Any, List, Optional

from agents.base import BaseAgent
from agents.worker import WorkerMixin
from models.task import Task
from services.llm import LLMClient


class ResearchAgent(WorkerMixin, BaseAgent):
    """
    Handles research-oriented tasks such as information gathering,
    requirements analysis, and summarising.

    Like :class:`~agents.coding.CodingAgent`, intelligence is injected: pass an
    :class:`~services.llm.LLMClient` for real model-backed research, or omit it
    for the deterministic offline placeholder.
    """

    capabilities: List[str] = ["research", "web", "summarise"]

    _PROMPT = (
        "You are a research analyst inside an autonomous agent runtime. "
        "Complete the following research task. Be concise and structured "
        "(short headings + bullet points); no preamble.\n\nTASK:\n{task}"
    )

    def __init__(self, llm: Optional[LLMClient] = None) -> None:
        self._llm = llm

    def execute(self, task: Task) -> Any:
        """
        Execute a research task — via the injected LLM when configured,
        otherwise a deterministic placeholder.
        """
        if self._llm is not None:
            return self._llm.complete(self._PROMPT.format(task=task.description))
        return f"[ResearchAgent] Executed: {task.description}"
