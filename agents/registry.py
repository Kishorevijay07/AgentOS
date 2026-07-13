from __future__ import annotations

import itertools
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from threading import Lock
from typing import Dict, List, Optional
from uuid import UUID

from agents.base import BaseAgent
from models.enums import AgentStatus
from models.task import Task


class AgentRecord:
    """
    Lightweight envelope that the registry stores for each registered agent.

    Attributes
    ----------
    agent_id:
        Human-readable unique identifier, e.g. ``"CodingAgent-1"``.
    agent:
        The live ``BaseAgent`` instance.
    capabilities:
        Mirror of ``agent.capabilities`` — stored here so the registry can
        query capabilities without importing every concrete agent class.
    status:
        Current lifecycle state (``IDLE`` / ``BUSY`` / ``OFFLINE``).
    current_task_id:
        UUID of the task the agent is currently executing, or ``None``.
    registered_at:
        UTC timestamp of registration.
    last_heartbeat:
        UTC timestamp of the most recent heartbeat ping.
    """

    def __init__(self, agent_id: str, agent: BaseAgent) -> None:
        self.agent_id: str = agent_id
        self.agent: BaseAgent = agent
        self.capabilities: List[str] = list(agent.capabilities)
        self.status: AgentStatus = AgentStatus.IDLE
        self.current_task_id: Optional[UUID] = None
        self.registered_at: datetime = datetime.now(timezone.utc)
        self.last_heartbeat: datetime = datetime.now(timezone.utc)

    def __repr__(self) -> str:
        return (
            f"AgentRecord(id={self.agent_id!r}, "
            f"status={self.status}, "
            f"caps={self.capabilities}, "
            f"task={self.current_task_id})"
        )


class AbstractAgentRegistry(ABC):
    """
    Contract that every Agent Registry backend must satisfy.

    Keeping the interface separate from the implementation means a future
    ``RedisAgentRegistry`` (for distributed / remote workers, with heartbeat
    TTLs) can slot in without changing the Scheduler or the Kernel — only the
    construction site changes.  The Scheduler depends on *this* type, never on
    :class:`AgentRegistry` directly (Dependency Inversion).
    """

    @abstractmethod
    def register(self, agent: BaseAgent) -> str:
        """Register a new agent instance and return its assigned ``agent_id``."""

    @abstractmethod
    def remove(self, agent_id: str) -> None:
        """Deregister an agent.  Raises ``KeyError`` if not found."""

    @abstractmethod
    def heartbeat(self, agent_id: str) -> datetime:
        """Update the agent's ``last_heartbeat`` and return the new timestamp."""

    @abstractmethod
    def get_capabilities(self, agent_id: str) -> List[str]:
        """Return the capability list for the given agent."""

    @abstractmethod
    def get_status(self, agent_id: str) -> AgentStatus:
        """Return the current ``AgentStatus`` of the given agent."""

    @abstractmethod
    def set_status(self, agent_id: str, status: AgentStatus) -> None:
        """Explicitly set the agent's status."""

    @abstractmethod
    def get_current_task(self, agent_id: str) -> Optional[UUID]:
        """Return the ``task_id`` the agent is currently working on."""

    @abstractmethod
    def set_current_task(self, agent_id: str, task_id: Optional[UUID]) -> None:
        """Update the agent's current task (``None`` clears it → IDLE)."""

    @abstractmethod
    def list_agents(self) -> List["AgentRecord"]:
        """Return a snapshot of all registered ``AgentRecord`` objects."""

    @abstractmethod
    def available_agents(self) -> List["AgentRecord"]:
        """Return only the agents currently in ``IDLE`` status."""

    @abstractmethod
    def mark_offline(self, agent_id: str) -> None:
        """Mark an agent as ``OFFLINE`` (e.g. missed heartbeat threshold)."""


class AgentRegistry(AbstractAgentRegistry):
    """
    Central registry for all live agent instances in AgentOS.

    Instead of hard-coding concrete agent classes at call-sites, any module
    that needs an agent asks the registry:

    >>> registry = AgentRegistry()
    >>> registry.register(CodingAgent())
    'CodingAgent-1'
    >>> agent = registry.get_agent_for_task(task)

    Thread-safe: all mutations acquire an internal ``Lock``.
    """

    # Counter per class name so IDs remain stable and human-readable.
    _counters: Dict[str, itertools.count] = {}  # class_name -> counter

    def __init__(self) -> None:
        self._records: Dict[str, AgentRecord] = {}  # agent_id -> AgentRecord
        self._lock = Lock()
        self._counters: Dict[str, itertools.count] = {}

    # ------------------------------------------------------------------ #
    #  Registration
    # ------------------------------------------------------------------ #

    def register(self, agent: BaseAgent) -> str:
        """
        Register a new agent instance and return its assigned ``agent_id``.

        The ID is ``"<ClassName>-<N>"`` (e.g. ``"CodingAgent-1"``).
        Registering the same *instance* twice raises ``ValueError``.
        """
        with self._lock:
            # Guard against double-registration of the same Python object.
            for record in self._records.values():
                if record.agent is agent:
                    raise ValueError(
                        f"Agent instance already registered as {record.agent_id!r}."
                    )

            class_name = type(agent).__name__
            if class_name not in self._counters:
                self._counters[class_name] = itertools.count(1)

            agent_id = f"{class_name}-{next(self._counters[class_name])}"
            self._records[agent_id] = AgentRecord(agent_id, agent)
            return agent_id

    def remove(self, agent_id: str) -> None:
        """
        Deregister an agent.  Raises ``KeyError`` if not found.
        """
        with self._lock:
            if agent_id not in self._records:
                raise KeyError(f"No agent registered with id {agent_id!r}.")
            del self._records[agent_id]

    # ------------------------------------------------------------------ #
    #  Heartbeat
    # ------------------------------------------------------------------ #

    def heartbeat(self, agent_id: str) -> datetime:
        """
        Update the agent's ``last_heartbeat`` to the current UTC time.

        Returns the new heartbeat timestamp.
        Raises ``KeyError`` if the agent is not registered.
        """
        with self._lock:
            record = self._get_record(agent_id)
            record.last_heartbeat = datetime.now(timezone.utc)
            # Bring an OFFLINE agent back to IDLE on heartbeat.
            if record.status == AgentStatus.OFFLINE:
                record.status = AgentStatus.IDLE
            return record.last_heartbeat

    # ------------------------------------------------------------------ #
    #  Capabilities
    # ------------------------------------------------------------------ #

    def get_capabilities(self, agent_id: str) -> List[str]:
        """Return the capability list for the given agent."""
        with self._lock:
            return list(self._get_record(agent_id).capabilities)

    # ------------------------------------------------------------------ #
    #  Status
    # ------------------------------------------------------------------ #

    def get_status(self, agent_id: str) -> AgentStatus:
        """Return the current ``AgentStatus`` of the given agent."""
        with self._lock:
            return self._get_record(agent_id).status

    def set_status(self, agent_id: str, status: AgentStatus) -> None:
        """Explicitly set the agent's status (used by the Scheduler)."""
        with self._lock:
            self._get_record(agent_id).status = status

    # ------------------------------------------------------------------ #
    #  Current Task
    # ------------------------------------------------------------------ #

    def get_current_task(self, agent_id: str) -> Optional[UUID]:
        """Return the ``task_id`` the agent is currently working on."""
        with self._lock:
            return self._get_record(agent_id).current_task_id

    def set_current_task(self, agent_id: str, task_id: Optional[UUID]) -> None:
        """
        Update the agent's current task.

        Pass ``None`` to clear it (agent becomes IDLE).
        Automatically transitions status between BUSY and IDLE.
        """
        with self._lock:
            record = self._get_record(agent_id)
            record.current_task_id = task_id
            record.status = AgentStatus.BUSY if task_id is not None else AgentStatus.IDLE

    # ------------------------------------------------------------------ #
    #  Lookup Helpers
    # ------------------------------------------------------------------ #

    def get_agent_for_task(self, task: Task) -> Optional[AgentRecord]:
        """
        Return the first IDLE agent whose capabilities satisfy all of the
        task's ``required_capabilities``.

        Returns ``None`` when no matching agent is available.
        """
        required = set(task.required_capabilities)
        with self._lock:
            for record in self._records.values():
                if record.status == AgentStatus.IDLE:
                    if required.issubset(set(record.capabilities)):
                        return record
        return None

    def list_agents(self) -> List[AgentRecord]:
        """Return a snapshot of all registered ``AgentRecord`` objects."""
        with self._lock:
            return list(self._records.values())

    def available_agents(self) -> List[AgentRecord]:
        """Return only the agents currently in ``IDLE`` status."""
        with self._lock:
            return [r for r in self._records.values() if r.status == AgentStatus.IDLE]

    def mark_offline(self, agent_id: str) -> None:
        """
        Mark an agent as ``OFFLINE`` (e.g. missed heartbeat threshold).
        Preserves the current task reference so the Scheduler can reassign it.
        """
        with self._lock:
            self._get_record(agent_id).status = AgentStatus.OFFLINE

    # ------------------------------------------------------------------ #
    #  Internals
    # ------------------------------------------------------------------ #

    def _get_record(self, agent_id: str) -> AgentRecord:
        """Retrieve a record or raise ``KeyError``. Must be called under lock."""
        record = self._records.get(agent_id)
        if record is None:
            raise KeyError(f"No agent registered with id {agent_id!r}.")
        return record

    def __len__(self) -> int:
        """Total number of registered agents."""
        with self._lock:
            return len(self._records)

    def __repr__(self) -> str:
        with self._lock:
            return f"AgentRegistry(agents={list(self._records.keys())})"
