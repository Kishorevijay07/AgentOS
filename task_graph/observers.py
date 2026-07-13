from __future__ import annotations

from typing import Protocol, runtime_checkable

from events.bus import AbstractEventBus
from events.event import Event
from events.event_type import EventType
from task_graph.node import TaskNode


@runtime_checkable
class GraphObserver(Protocol):
    """
    Port for reacting to node lifecycle changes in the graph.

    This is how the graph "notifies the Scheduler" **without knowing what a
    scheduler is**. The graph calls these hooks; whoever wants to react
    (a scheduler waking up, a metrics sink, an event-bus bridge) implements the
    port. Keeping it a Protocol means observers need no shared base class.

    All hooks must be side-effect-tolerant and fast — the graph invokes them
    while holding no lock, but on the thread that triggered the transition.
    """

    def on_ready(self, node: TaskNode) -> None:
        """A node's dependencies were satisfied; it is now eligible to run."""
        ...

    def on_completed(self, node: TaskNode) -> None:
        """A node finished successfully."""
        ...

    def on_failed(self, node: TaskNode) -> None:
        """A node failed."""
        ...


class EventBusGraphObserver:
    """
    Bridges graph notifications onto the AgentOS :class:`AbstractEventBus`.

    Inject this observer to publish ``TASK_READY`` / ``TASK_COMPLETED`` /
    ``TASK_FAILED`` events as the DAG advances, so the rest of the runtime
    observes graph progress through the same bus it already uses — with the
    graph core itself staying free of any events dependency.
    """

    def __init__(self, bus: AbstractEventBus) -> None:
        self._bus = bus

    def on_ready(self, node: TaskNode) -> None:
        self._publish(EventType.TASK_READY, node)

    def on_completed(self, node: TaskNode) -> None:
        self._publish(EventType.TASK_COMPLETED, node)

    def on_failed(self, node: TaskNode) -> None:
        self._publish(EventType.TASK_FAILED, node)

    def _publish(self, event_type: EventType, node: TaskNode) -> None:
        self._bus.publish(
            Event(
                type=event_type,
                payload={
                    "task_id": str(node.task_id),
                    "state": node.state.value,
                    "assigned_worker": node.assigned_worker,
                },
                source="TaskGraph",
            )
        )
