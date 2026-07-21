"""
Unit tests for RedisTransport — fake Redis client, no server required.

The fake emulates exactly the redis-py surface the transport uses
(``publish``, ``pubsub().subscribe/unsubscribe/get_message/close``), so these
tests exercise the real listener thread, codec round-trip, and fan-out logic.
"""
from __future__ import annotations

import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

import pytest

from distributed import (
    Channels,
    DistributedScheduler,
    RedisTransport,
    RemoteWorkerNode,
    WorkerDirectory,
)
from distributed.messages import HeartbeatMessage, Message, RegisterMessage
from models.task import Task
from task_graph import InMemoryTaskGraph


# ---------------------------------------------------------------------------
# Fake redis-py client
# ---------------------------------------------------------------------------

class _FakePubSub:
    def __init__(self, server: "_FakeRedis") -> None:
        self._server = server
        self._queue: "queue.Queue[dict]" = queue.Queue()
        self.channels: set[str] = set()
        self._closed = False

    def subscribe(self, *topics: str) -> None:
        self.channels.update(topics)

    def unsubscribe(self, *topics: str) -> None:
        self.channels.difference_update(topics)

    def get_message(self, timeout: float = 0.0):
        if self._closed:
            raise ConnectionError("pubsub closed")
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self._closed = True

    # called by the fake server
    def deliver(self, channel: str, data: str) -> None:
        if channel in self.channels and not self._closed:
            self._queue.put({"type": "message", "channel": channel, "data": data})


class _FakeRedis:
    """In-memory stand-in for redis.Redis limited to pub/sub."""

    def __init__(self) -> None:
        self._pubsubs: List[_FakePubSub] = []
        self._lock = threading.Lock()
        self.published: List[tuple] = []

    def pubsub(self, *, ignore_subscribe_messages: bool = True) -> _FakePubSub:
        ps = _FakePubSub(self)
        with self._lock:
            self._pubsubs.append(ps)
        return ps

    def publish(self, channel: str, data: str) -> int:
        self.published.append((channel, data))
        with self._lock:
            targets = list(self._pubsubs)
        for ps in targets:
            ps.deliver(channel, data)
        return len(targets)


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


@pytest.fixture()
def server() -> _FakeRedis:
    return _FakeRedis()


def _transport(server: _FakeRedis) -> RedisTransport:
    return RedisTransport(client=server)


# ---------------------------------------------------------------------------
# Transport behaviour
# ---------------------------------------------------------------------------

class TestRedisTransport:
    def test_publish_reaches_subscriber_via_listener_thread(self, server):
        transport = _transport(server)
        received: List[Message] = []
        transport.subscribe(Channels.HEARTBEAT, received.append)
        transport.start()
        try:
            transport.publish(
                Channels.HEARTBEAT, HeartbeatMessage(sender="w1", worker_id="w1")
            )
            assert _wait_for(lambda: len(received) == 1)
            assert isinstance(received[0], HeartbeatMessage)
            assert received[0].worker_id == "w1"
        finally:
            transport.stop()

    def test_wire_format_is_codec_json(self, server):
        transport = _transport(server)
        transport.publish(Channels.REGISTER, RegisterMessage(sender="w1", worker_id="w1"))
        channel, payload = server.published[0]
        assert channel == Channels.REGISTER
        assert '"type":"register"' in payload.replace(" ", "")

    def test_subscribe_before_start_is_honoured(self, server):
        transport = _transport(server)
        received: List[Message] = []
        transport.subscribe("topic.x", received.append)  # before start()
        transport.start()
        try:
            transport.publish("topic.x", HeartbeatMessage(sender="w", worker_id="w"))
            assert _wait_for(lambda: len(received) == 1)
        finally:
            transport.stop()

    def test_unsubscribed_topic_not_delivered(self, server):
        transport = _transport(server)
        received: List[Message] = []
        transport.subscribe("topic.a", received.append)
        transport.start()
        try:
            transport.unsubscribe("topic.a", received.append)
            transport.publish("topic.a", HeartbeatMessage(sender="w", worker_id="w"))
            time.sleep(0.1)
            assert received == []
        finally:
            transport.stop()

    def test_bad_handler_does_not_break_others(self, server):
        transport = _transport(server)
        good: List[Message] = []

        def boom(_m):
            raise RuntimeError("bad subscriber")

        transport.subscribe(Channels.HEARTBEAT, boom)
        transport.subscribe(Channels.HEARTBEAT, good.append)
        transport.start()
        try:
            transport.publish(Channels.HEARTBEAT, HeartbeatMessage(sender="w", worker_id="w"))
            assert _wait_for(lambda: len(good) == 1)
        finally:
            transport.stop()

    def test_undecodable_payload_dropped_gracefully(self, server):
        transport = _transport(server)
        received: List[Message] = []
        transport.subscribe("topic.z", received.append)
        transport.start()
        try:
            server.publish("topic.z", "not json at all")
            server.publish("topic.z", '{"type": "heartbeat", "sender": "w", "worker_id": "w"}')
            assert _wait_for(lambda: len(received) == 1)  # bad one dropped, good one delivered
        finally:
            transport.stop()


# ---------------------------------------------------------------------------
# Full distributed stack over the fake Redis — the broker-swap proof
# ---------------------------------------------------------------------------

class _CodeWorker:
    capabilities: List[str] = ["code"]

    def initialize(self) -> None: ...
    def execute(self, task: Task) -> Any:
        return f"done: {task.description}"
    def heartbeat(self) -> datetime:
        return datetime.now(timezone.utc)
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def shutdown(self) -> None: ...


class TestBrokerSwapEndToEnd:
    def test_full_dag_runs_over_redis_transport(self, server):
        """
        Identical wiring to the InMemoryTransport integration test — only the
        transport construction changed. That is ADR-0008's promise, executed.
        """
        transport = _transport(server)
        transport.start()
        directory = WorkerDirectory(transport)
        directory.start()

        node = RemoteWorkerNode(_CodeWorker(), transport, worker_id="coder-1")
        node.start(start_heartbeat=False)

        assert _wait_for(lambda: len(directory.available_workers()) == 1)

        graph = InMemoryTaskGraph()
        a = Task(description="a", required_capabilities=["code"])
        b = Task(description="b", required_capabilities=["code"], dependencies=[a.id])
        graph.add_task(a)
        graph.add_task(b)

        scheduler = DistributedScheduler(graph, directory, transport)
        scheduler.start()
        scheduler.run_until_idle(timeout=10.0)

        assert len(graph.completed_tasks()) == 2
        assert graph.has_active_work() is False

        node.stop()
        scheduler.stop()
        transport.stop()
