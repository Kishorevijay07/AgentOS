from __future__ import annotations

from typing import Dict, List, Optional, Type

from agents.base import BaseAgent


class AgentRegistry:
    """
    Maintains the pool of registered agents.

    Agents are stored by their class name so the Scheduler can look them
    up quickly when routing a task.
    """

    def __init__(self) -> None:
        self._agents: Dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        """
        Register a concrete agent instance.

        Parameters
        ----------
        agent:
            An instantiated agent whose ``capabilities`` list is already set.
        """
        key = type(agent).__name__
        self._agents[key] = agent

    def all_agents(self) -> List[BaseAgent]:
        """Return all registered agent instances."""
        return list(self._agents.values())

    def get(self, name: str) -> Optional[BaseAgent]:
        """Return an agent by its class name, or None if not found."""
        return self._agents.get(name)

    def __repr__(self) -> str:
        names = list(self._agents.keys())
        return f"AgentRegistry(agents={names})"
