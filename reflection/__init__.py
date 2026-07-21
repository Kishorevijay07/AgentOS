"""
reflection — the AgentOS Autonomous Loop (v0.8).

Closes the loop on a one-shot pipeline: after a task completes, a
:class:`Reflector` judges the output and — when it falls short — the
:class:`ReflectionCoordinator` injects corrective/follow-up tasks into the
**live** task graph. The next scheduler wave runs them, so a goal expands itself
until the work is genuinely done (bounded by a hard replan budget).

Reflection is planning's mirror image and shares its shape (models → prompt →
parser → strategy → coordinator). It is entirely opt-in: with no coordinator
wired into the Kernel, behaviour is unchanged.

Quick start
-----------
>>> from reflection import ReflectionCoordinator, LLMReflector
>>> coordinator = ReflectionCoordinator(graph, LLMReflector(llm), goal="…")
>>> coordinator.process(tick_outcomes)   # injects follow-ups into the graph
"""

from reflection.coordinator import ReflectionCoordinator
from reflection.models import (
    ProposedTask,
    ReflectionDecision,
    ReflectionRequest,
    ReflectionVerdict,
)
from reflection.parser import ReflectionParser
from reflection.prompts import DefaultReflectionPrompt, ReflectionPrompt
from reflection.reflector import HeuristicReflector, LLMReflector, Reflector

__all__ = [
    "ReflectionCoordinator",
    "Reflector",
    "HeuristicReflector",
    "LLMReflector",
    "ReflectionPrompt",
    "DefaultReflectionPrompt",
    "ReflectionParser",
    "ReflectionVerdict",
    "ReflectionRequest",
    "ProposedTask",
    "ReflectionDecision",
]
