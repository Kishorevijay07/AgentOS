from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections import deque
from typing import Callable, Deque, Dict, List, Optional

from events.event import Event
from events.event_type import EventType

# Type alias for subscriber callables.
Handler = Callable[[Event], None]


# --------------------------------------------------------------------------- #
#  Abstract interface                                                           #
# --------------------------------------------------------------------------- #

class AbstractEventBus(ABC):
    """
    Contract that every Event Bus backend must satisfy.

    Keeping the interface separate from the implementation means a future
    ``RedisEventBus`` or ``KafkaEventBus`` can slot in without changing any
    publisher or subscriber code — only the construction site changes.
    """

    @abstractmethod
    def subscribe(self, event_type: EventType, handler: Handler) -> None:
        """Register *handler* to be called whenever *event_type* is published."""

    @abstractmethod
    def unsubscribe(self, event_type: EventType, handler: Handler) -> None:
        """Remove a previously registered handler (no-op if not found)."""

    @abstractmethod
    def publish(self, event: Event) -> None:
        """Deliver *event* synchronously to all registered subscribers."""

    @abstractmethod
    def history(self, n: int = 50) -> List[Event]:
        """Return the *n* most-recent events seen by this bus."""


# --------------------------------------------------------------------------- #
#  In-memory implementation                                                    #
# --------------------------------------------------------------------------- #

class InMemoryEventBus(AbstractEventBus):
    """
    Synchronous, in-process Event Bus backed by plain Python data structures.

    Design decisions
    ----------------
    * **Synchronous by default** — ``publish`` calls every handler in the
      calling thread before returning.  This makes behaviour deterministic and
      trivial to test.
    * **publish_async** — wraps ``publish`` in a daemon ``Thread`` for
      fire-and-forget notifications that must not block the caller.
    * **History ring-buffer** — the last ``_MAX_HISTORY`` events are retained
      for debugging and replay.  Oldest entries are discarded automatically.
    * **Thread-safe** — a single ``Lock`` guards both the subscriber map and
      the history deque.

    Swapping to Redis / Kafka
    -------------------------
    Implement ``AbstractEventBus`` in a new class, inject it wherever
    ``InMemoryEventBus`` is currently injected.  No subscriber or publisher
    code needs to change.
    """

    _MAX_HISTORY: int = 500

    def __init__(self) -> None:
        self._subscribers: Dict[EventType, List[Handler]] = {}
        self._history: Deque[Event] = deque(maxlen=self._MAX_HISTORY)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    #  Subscription management                                            #
    # ------------------------------------------------------------------ #

    def subscribe(self, event_type: EventType, handler: Handler) -> None:
        """
        Register *handler* to be called whenever *event_type* is published.

        The same handler can be registered for multiple event types.
        Registering the same (event_type, handler) pair twice is a no-op.
        """
        with self._lock:
            bucket = self._subscribers.setdefault(event_type, [])
            if handler not in bucket:
                bucket.append(handler)

    def unsubscribe(self, event_type: EventType, handler: Handler) -> None:
        """
        Remove *handler* from the *event_type* subscriber list.

        Silent no-op if the handler was never registered.
        """
        with self._lock:
            bucket = self._subscribers.get(event_type, [])
            try:
                bucket.remove(handler)
            except ValueError:
                pass

    # ------------------------------------------------------------------ #
    #  Publishing                                                         #
    # ------------------------------------------------------------------ #

    def publish(self, event: Event) -> None:
        """
        Deliver *event* to all subscribers registered for ``event.type``.

        Handlers are called **synchronously** in registration order.
        Exceptions raised by a handler are caught and printed to stderr
        so that one bad subscriber cannot silently break others.
        """
        with self._lock:
            handlers = list(self._subscribers.get(event.type, []))
            self._history.append(event)

        for handler in handlers:
            try:
                handler(event)
            except Exception as exc:  # noqa: BLE001
                import sys
                print(
                    f"[EventBus] Handler {handler!r} raised on {event.type!r}: {exc}",
                    file=sys.stderr,
                )

    def publish_async(self, event: Event) -> None:
        """
        Deliver *event* in a background daemon thread (fire-and-forget).

        Use this for notifications that must not block the caller, such as
        heartbeat pings or metric updates.  For anything that requires
        sequential consistency, prefer :meth:`publish`.
        """
        t = threading.Thread(target=self.publish, args=(event,), daemon=True)
        t.start()

    # ------------------------------------------------------------------ #
    #  Inspection                                                         #
    # ------------------------------------------------------------------ #

    def history(self, n: int = 50) -> List[Event]:
        """Return the *n* most-recent events (newest last)."""
        with self._lock:
            events = list(self._history)
        return events[-n:]

    def subscriber_count(self, event_type: EventType) -> int:
        """Return how many handlers are registered for *event_type*."""
        with self._lock:
            return len(self._subscribers.get(event_type, []))

    def clear_history(self) -> None:
        """Flush the event history ring-buffer (useful in tests)."""
        with self._lock:
            self._history.clear()

    def __repr__(self) -> str:
        with self._lock:
            total_subs = sum(len(v) for v in self._subscribers.values())
            return (
                f"InMemoryEventBus("
                f"subscribers={total_subs}, "
                f"history={len(self._history)})"
            )


# --------------------------------------------------------------------------- #
#  Global singleton                                                            #
# --------------------------------------------------------------------------- #

_bus_lock = threading.Lock()
_default_bus: Optional[InMemoryEventBus] = None


def get_event_bus() -> InMemoryEventBus:
    """
    Return the process-wide default Event Bus, creating it on first call.

    Use this when you want a shared bus without explicit dependency injection.
    For testing, create a fresh ``InMemoryEventBus()`` and inject it directly
    so tests remain isolated.

    Example
    -------
    >>> from events import get_event_bus, EventType
    >>> bus = get_event_bus()
    >>> bus.subscribe(EventType.TASK_COMPLETED, lambda e: print(e))
    """
    global _default_bus
    with _bus_lock:
        if _default_bus is None:
            _default_bus = InMemoryEventBus()
    return _default_bus
