from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID


@dataclass
class AgentResult:
    """
    Envelope that wraps the output of a completed agent execution.

    Workers never communicate directly with each other or with the Supervisor.
    Instead, every execution produces one ``AgentResult`` that is pushed onto
    the ``ResultQueue``; the Supervisor reads from there.
    """

    task_id: UUID
    """UUID of the ``Task`` this result belongs to."""

    agent_name: str
    """Class name of the agent that produced this result."""

    output: Any
    """Raw return value from ``agent.execute(task)``."""

    success: bool
    """True when the agent completed without raising an exception."""

    error: Optional[str] = None
    """Human-readable error message if ``success`` is False."""

    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    """UTC timestamp of result creation."""
