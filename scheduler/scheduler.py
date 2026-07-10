from __future__ import annotations

from typing import List, Optional

from agents.base import BaseAgent
from models.task import Task
from scheduler.registry import AgentRegistry


class Scheduler:
    """
    Capability-based task scheduler.

    The Scheduler never inspects task description strings with brittle
    keyword checks.  Instead, each ``Task`` declares the capabilities it
    *requires* (``task.required_capabilities``), and the Scheduler finds
    the best-matching registered agent by comparing those requirements
    against each agent's ``capabilities`` list.

    Matching algorithm
    ------------------
    1. Filter agents that satisfy *all* required capabilities.
    2. Among those candidates, prefer the agent with the *highest* coverage
       score — i.e., the one whose capability list is the smallest superset
       of the required capabilities (fewest unneeded extras).
    3. If no agent satisfies all requirements, fall back to the agent with
       the highest *partial* match count.
    4. If still no agent is found, ``dispatch`` returns ``None``.
    """

    def __init__(self, registry: AgentRegistry) -> None:
        self._registry = registry

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def dispatch(self, task: Task) -> Optional[BaseAgent]:
        """
        Find the most-suitable agent for ``task`` and return it.

        The task is NOT executed here — the caller decides when to call
        ``agent.execute(task)``.  This keeps scheduling and execution
        concerns separate.

        Parameters
        ----------
        task:
            The task to route.  Must have a ``required_capabilities``
            attribute (list of strings).

        Returns
        -------
        BaseAgent | None
            The best-matching agent, or ``None`` if no agent is registered.
        """
        required: List[str] = getattr(task, "required_capabilities", [])
        agents = self._registry.all_agents()

        if not agents:
            return None

        # --- exact / full-match candidates ---
        full_matches = [a for a in agents if self._covers(a, required)]

        if full_matches:
            # Among full matches, prefer the most specialised agent
            # (smallest capabilities list = fewest unneeded skills).
            return min(full_matches, key=lambda a: len(a.capabilities))

        # --- partial-match fallback ---
        return max(agents, key=lambda a: self._overlap(a, required))

    # ------------------------------------------------------------------ #
    #  Private helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _covers(agent: BaseAgent, required: List[str]) -> bool:
        """Return True if the agent satisfies every required capability."""
        agent_caps = set(agent.capabilities)
        return all(cap in agent_caps for cap in required)

    @staticmethod
    def _overlap(agent: BaseAgent, required: List[str]) -> int:
        """Return the number of required capabilities the agent satisfies."""
        agent_caps = set(agent.capabilities)
        return sum(1 for cap in required if cap in agent_caps)
