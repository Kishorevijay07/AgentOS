"""Unit tests for events/bus.py (Module 5 — Event Bus)."""
from __future__ import annotations

import time
from typing import List

import pytest

from events.bus import InMemoryEventBus
from events.event import Event
from events.event_type import EventType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(event_type: EventType = EventType.TASK_CREATED, source: str = "test") -> Event:
    return Event(type=event_type, payload={"key": "value"}, source=source)


def _collector() -> tuple[List[Event], callable]:
    """Return (list, handler) — handler appends to the list."""
    received: List[Event] = []
    return received, lambda e: received.append(e)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def bus() -> InMemoryEventBus:
    return InMemoryEventBus()


# ---------------------------------------------------------------------------
# subscribe / unsubscribe
# ---------------------------------------------------------------------------

class TestSubscribe:
    def test_subscribe_receives_published_event(self, bus):
        received, handler = _collector()
        bus.subscribe(EventType.TASK_CREATED, handler)
        event = _make_event(EventType.TASK_CREATED)
        bus.publish(event)
        assert received == [event]

    def test_subscribe_same_handler_twice_is_idempotent(self, bus):
        received, handler = _collector()
        bus.subscribe(EventType.TASK_CREATED, handler)
        bus.subscribe(EventType.TASK_CREATED, handler)  # duplicate — no-op
        bus.publish(_make_event(EventType.TASK_CREATED))
        assert len(received) == 1

    def test_subscriber_count_increments(self, bus):
        _, h1 = _collector()
        _, h2 = _collector()
        bus.subscribe(EventType.TASK_ASSIGNED, h1)
        bus.subscribe(EventType.TASK_ASSIGNED, h2)
        assert bus.subscriber_count(EventType.TASK_ASSIGNED) == 2

    def test_no_subscribers_publish_is_silent(self, bus):
        # Should not raise
        bus.publish(_make_event(EventType.TASK_COMPLETED))

    def test_subscriber_only_receives_its_event_type(self, bus):
        received, handler = _collector()
        bus.subscribe(EventType.TASK_CREATED, handler)
        bus.publish(_make_event(EventType.TASK_COMPLETED))  # different type
        assert received == []


class TestUnsubscribe:
    def test_unsubscribe_stops_delivery(self, bus):
        received, handler = _collector()
        bus.subscribe(EventType.TASK_FAILED, handler)
        bus.unsubscribe(EventType.TASK_FAILED, handler)
        bus.publish(_make_event(EventType.TASK_FAILED))
        assert received == []

    def test_unsubscribe_unknown_handler_is_silent(self, bus):
        _, handler = _collector()
        # Never subscribed — should not raise
        bus.unsubscribe(EventType.TASK_CREATED, handler)

    def test_unsubscribe_decrements_count(self, bus):
        _, handler = _collector()
        bus.subscribe(EventType.AGENT_ONLINE, handler)
        assert bus.subscriber_count(EventType.AGENT_ONLINE) == 1
        bus.unsubscribe(EventType.AGENT_ONLINE, handler)
        assert bus.subscriber_count(EventType.AGENT_ONLINE) == 0


# ---------------------------------------------------------------------------
# publish — synchronous delivery
# ---------------------------------------------------------------------------

class TestPublish:
    def test_publish_delivers_to_multiple_subscribers(self, bus):
        r1, h1 = _collector()
        r2, h2 = _collector()
        bus.subscribe(EventType.TASK_STARTED, h1)
        bus.subscribe(EventType.TASK_STARTED, h2)
        event = _make_event(EventType.TASK_STARTED)
        bus.publish(event)
        assert r1 == [event]
        assert r2 == [event]

    def test_publish_payload_is_preserved(self, bus):
        received, handler = _collector()
        bus.subscribe(EventType.TASK_ASSIGNED, handler)
        event = Event(
            type=EventType.TASK_ASSIGNED,
            payload={"task_id": "abc-123", "agent_id": "CodingAgent-1"},
            source="Scheduler",
        )
        bus.publish(event)
        assert received[0].payload["task_id"] == "abc-123"
        assert received[0].source == "Scheduler"

    def test_failing_handler_does_not_break_other_subscribers(self, bus):
        """A bad handler must not prevent the second handler from running."""
        def bad_handler(_event: Event) -> None:
            raise RuntimeError("boom")

        good_received: List[Event] = []
        bus.subscribe(EventType.TASK_COMPLETED, bad_handler)
        bus.subscribe(EventType.TASK_COMPLETED, lambda e: good_received.append(e))
        event = _make_event(EventType.TASK_COMPLETED)
        bus.publish(event)  # should not raise
        assert good_received == [event]

    def test_publish_multiple_events_in_order(self, bus):
        received, handler = _collector()
        bus.subscribe(EventType.TASK_CREATED, handler)
        e1 = _make_event(EventType.TASK_CREATED, source="A")
        e2 = _make_event(EventType.TASK_CREATED, source="B")
        bus.publish(e1)
        bus.publish(e2)
        assert received == [e1, e2]


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------

class TestHistory:
    def test_history_records_published_events(self, bus):
        event = _make_event(EventType.AGENT_ONLINE)
        bus.publish(event)
        assert event in bus.history()

    def test_history_respects_n_limit(self, bus):
        for _ in range(10):
            bus.publish(_make_event(EventType.TASK_CREATED))
        assert len(bus.history(n=3)) == 3

    def test_history_returns_most_recent_n(self, bus):
        events = [_make_event(EventType.TASK_CREATED, source=str(i)) for i in range(5)]
        for e in events:
            bus.publish(e)
        recent = bus.history(n=2)
        assert recent == events[-2:]

    def test_clear_history_empties_buffer(self, bus):
        bus.publish(_make_event(EventType.TASK_CREATED))
        bus.clear_history()
        assert bus.history() == []

    def test_history_without_subscribers(self, bus):
        """Events published without any subscriber still appear in history."""
        event = _make_event(EventType.TASK_FAILED)
        bus.publish(event)
        assert event in bus.history()


# ---------------------------------------------------------------------------
# publish_async — fire-and-forget
# ---------------------------------------------------------------------------

class TestPublishAsync:
    def test_publish_async_delivers_event(self, bus):
        received, handler = _collector()
        bus.subscribe(EventType.AGENT_HEARTBEAT, handler)
        event = _make_event(EventType.AGENT_HEARTBEAT)
        bus.publish_async(event)
        # Give the daemon thread time to complete
        time.sleep(0.1)
        assert received == [event]

    def test_publish_async_does_not_block(self, bus):
        """publish_async should return quickly even for slow handlers."""
        slow_received: List[Event] = []

        def slow_handler(e: Event) -> None:
            time.sleep(0.05)
            slow_received.append(e)

        bus.subscribe(EventType.TASK_STARTED, slow_handler)
        event = _make_event(EventType.TASK_STARTED)

        start = time.monotonic()
        bus.publish_async(event)
        elapsed = time.monotonic() - start

        assert elapsed < 0.04  # returned before the handler even finished
        time.sleep(0.1)         # let thread finish
        assert slow_received == [event]


# ---------------------------------------------------------------------------
# EventType enum
# ---------------------------------------------------------------------------

class TestEventType:
    def test_all_event_types_exist(self):
        expected = {
            "TASK_CREATED", "TASK_READY", "TASK_ASSIGNED", "TASK_STARTED",
            "TASK_COMPLETED", "TASK_FAILED",
            "AGENT_ONLINE", "AGENT_OFFLINE", "AGENT_HEARTBEAT",
        }
        actual = {e.name for e in EventType}
        assert expected == actual

    def test_event_type_values_are_strings(self):
        for et in EventType:
            assert isinstance(et.value, str)


# ---------------------------------------------------------------------------
# Event dataclass
# ---------------------------------------------------------------------------

class TestEvent:
    def test_event_has_auto_uuid(self):
        e1 = _make_event()
        e2 = _make_event()
        assert e1.id != e2.id

    def test_event_has_utc_timestamp(self):
        from datetime import timezone
        e = _make_event()
        assert e.timestamp.tzinfo == timezone.utc

    def test_event_repr_contains_type_and_source(self):
        e = Event(type=EventType.TASK_ASSIGNED, payload={}, source="Scheduler")
        assert "task.assigned" in repr(e)
        assert "Scheduler" in repr(e)
