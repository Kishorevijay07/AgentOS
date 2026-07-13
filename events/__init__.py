"""
events — AgentOS inter-component messaging layer.

Nothing calls another component directly.
Components publish Events; subscribers react.

Quick-start
-----------
>>> from events import get_event_bus, Event, EventType
>>> bus = get_event_bus()
>>> bus.subscribe(EventType.TASK_COMPLETED, lambda e: print("done!", e.payload))
>>> bus.publish(Event(type=EventType.TASK_COMPLETED, payload={"task_id": "..."}, source="Supervisor"))
"""

from events.bus import AbstractEventBus, InMemoryEventBus, get_event_bus
from events.event import Event
from events.event_type import EventType

__all__ = [
    "AbstractEventBus",
    "InMemoryEventBus",
    "get_event_bus",
    "Event",
    "EventType",
]
