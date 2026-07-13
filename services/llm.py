from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """
    Minimal port for a large-language-model completion backend.

    This is the seam that isolates *everything network- and model-specific* from
    the rest of AgentOS. Anything that reasons with an LLM (the
    :class:`~planning.planner.LLMPlanner`, future Reflection/Replanning agents)
    depends only on this Protocol, so the concrete model can be swapped —
    Anthropic today, an MCP-backed client, or a deterministic fake in tests —
    without touching a single consumer.

    A Protocol (structural typing) is used deliberately: a backend does not need
    to import or subclass anything to satisfy the port; it just needs a matching
    ``complete`` method. That keeps AgentOS free of a hard dependency on any SDK.
    """

    def complete(self, prompt: str) -> str:
        """
        Return the model's completion for *prompt*.

        Implementations should raise on transport/model failure; callers
        (e.g. the planner) translate that into a domain error.
        """
        ...


class StaticLLMClient:
    """
    Deterministic :class:`LLMClient` for tests, examples, and offline runs.

    It returns a pre-seeded ``response`` regardless of the prompt — so a test
    can feed the planner an exact payload, and a demo can run with no API key or
    network. It is the fake half of the port; the real half is an SDK-backed
    client injected in production.
    """

    def __init__(self, response: str) -> None:
        self._response = response
        self.calls: list[str] = []  # captured prompts, for test assertions

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self._response
