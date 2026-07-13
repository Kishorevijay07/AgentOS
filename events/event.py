from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict
from uuid import UUID, uuid4

from events.event_type import EventType


@dataclass
class Event:
    """
    Immutable envelope that wraps every message on the Event Bus.

    All inter-component communication in AgentOS travels as ``Event``
    objects.  Publishers never call component methods directly; they
    post an ``Event`` and walk away.

    Attributes
    ----------
    type:
        The :class:`EventType` discriminator — subscribers filter on this.
    payload:
        Arbitrary JSON-serialisable data specific to the event type.
        Convention: use snake_case keys.  See each ``EventType`` for the
        expected payload schema.
    source:
        Human-readable name of the component that published this event,
        e.g. ``"Scheduler"``, ``"CodingAgent-1"``.
    id:
        Auto-generated UUID — unique per event instance, useful for
        de-duplication and tracing.
    timestamp:
        UTC creation time.  Set automatically; publishers should not
        override it.

    Example payloads
    ----------------
    TASK_ASSIGNED  → {"task_id": "<uuid>", "agent_id": "CodingAgent-1", "capabilities": ["code"]}
    TASK_COMPLETED → {"task_id": "<uuid>", "agent_id": "CodingAgent-1", "result": "..."}
    AGENT_ONLINE   → {"agent_id": "CodingAgent-1", "capabilities": ["code", "debug"]}
    """

    type: EventType
    payload: Dict[str, Any]
    source: str
    id: UUID = field(default_factory=uuid4)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        return (
            f"Event(type={self.type!r}, source={self.source!r}, "
            f"id={self.id}, ts={self.timestamp.isoformat()})"
        )
