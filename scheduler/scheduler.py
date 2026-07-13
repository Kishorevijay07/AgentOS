from __future__ import annotations

from typing import List, Optional

from agents.registry import AbstractAgentRegistry, AgentRecord
from events.bus import AbstractEventBus
from events.event import Event
from events.event_type import EventType
from models.enums import AgentStatus
from models.task import Task


class Scheduler:
    """
    Capability-based task scheduler — v2.

    v2 changes vs v1
    ----------------
    * Operates on the canonical ``AgentRegistry`` (from ``agents.registry``)
      instead of the thin ``scheduler/registry.py`` shim.
    * Returns an ``AgentRecord`` (not a raw ``BaseAgent``), giving the caller
      the full envelope — ``agent_id``, ``status``, ``current_task_id``.
    * Only considers **IDLE** agents — agents that are BUSY, PAUSED, or
      OFFLINE are skipped entirely.
    * Publishes a ``TASK_ASSIGNED`` event on the Event Bus after every
      successful dispatch.
    * The Scheduler **knows nothing about concrete agent classes**.  It
      queries capabilities only.

    Matching algorithm
    ------------------
    1. Filter to IDLE agents whose capabilities are a **superset** of the
       task's ``required_capabilities`` (full match).
    2. Among full-match candidates, prefer the **most specialised** agent
       — the one with the fewest total capabilities (smallest superset).
       This leaves generalist agents available for tasks that need them.
    3. If no full match exists, fall back to the agent with the highest
       **partial overlap** score (most required caps it does satisfy).
    4. If the queue of registered agents is empty, return ``None``.

    After dispatch
    --------------
    On success the Scheduler calls ``registry.set_current_task`` to mark the
    agent BUSY immediately, then publishes ``TASK_ASSIGNED`` to the Event Bus.
    """

    def __init__(
        self,
        registry: AbstractAgentRegistry,
        bus: Optional[AbstractEventBus] = None,
    ) -> None:
        """
        Parameters
        ----------
        registry:
            Any :class:`~agents.registry.AbstractAgentRegistry` implementation
            (in-memory ``AgentRegistry`` today; a Redis-backed registry later).
        bus:
            Optional event bus.  When provided, a ``TASK_ASSIGNED`` event is
            published after every successful dispatch.  Pass ``None`` to
            disable event publishing (useful in tests that don't need it).
        """
        self._registry = registry
        self._bus = bus

    # ------------------------------------------------------------------ #
    #  Public API                                                         #
    # ------------------------------------------------------------------ #

    def dispatch(self, task: Task) -> Optional[AgentRecord]:
        """
        Find the best IDLE agent for *task* and return its ``AgentRecord``.

        The task is **not** executed here — the Supervisor calls
        ``agent.execute(task)`` after receiving the record.  This keeps
        scheduling and execution concerns separate.

        Parameters
        ----------
        task:
            The task to route.  ``task.required_capabilities`` drives the
            matching logic.

        Returns
        -------
        AgentRecord | None
            The best-matching agent record, or ``None`` if no suitable
            IDLE agent is found.
        """
        required: List[str] = list(getattr(task, "required_capabilities", []))

        # Only consider IDLE agents — Scheduler never pre-empts busy workers.
        candidates: List[AgentRecord] = [
            r for r in self._registry.list_agents()
            if r.status == AgentStatus.IDLE
        ]

        if not candidates:
            return None

        # --- Full-match candidates ---
        full_matches = [r for r in candidates if self._covers(r, required)]

        if full_matches:
            # Most specialised = smallest capability set (fewest extras).
            winner = min(full_matches, key=lambda r: len(r.capabilities))
        else:
            # Partial fallback — best overlap.
            winner = max(candidates, key=lambda r: self._overlap(r, required))

        # Mark the agent busy and update current task atomically.
        self._registry.set_current_task(winner.agent_id, task.id)

        # Publish TASK_ASSIGNED event.
        if self._bus is not None:
            self._bus.publish(
                Event(
                    type=EventType.TASK_ASSIGNED,
                    payload={
                        "task_id": str(task.id),
                        "agent_id": winner.agent_id,
                        "capabilities": list(winner.capabilities),
                        "required_capabilities": required,
                    },
                    source="Scheduler",
                )
            )

        return winner

    def release(self, agent_id: str) -> None:
        """
        Return an agent to the IDLE pool after its task finishes.

        Symmetric counterpart to :meth:`dispatch`: ``dispatch`` acquires an idle
        agent (marks it BUSY), ``release`` frees it (clears its current task →
        IDLE) so it can be matched again. The Supervisor calls this once the
        agent's ``execute`` returns, whether the task succeeded or failed.

        A ``KeyError`` (agent already deregistered) is swallowed — releasing a
        gone agent is a no-op.
        """
        try:
            self._registry.set_current_task(agent_id, None)
        except KeyError:
            pass

    # ------------------------------------------------------------------ #
    #  Private helpers                                                    #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _covers(record: AgentRecord, required: List[str]) -> bool:
        """Return True if the agent satisfies every required capability."""
        agent_caps = set(record.capabilities)
        return all(cap in agent_caps for cap in required)

    @staticmethod
    def _overlap(record: AgentRecord, required: List[str]) -> int:
        """Return the number of required capabilities the agent satisfies."""
        agent_caps = set(record.capabilities)
        return sum(1 for cap in required if cap in agent_caps)
