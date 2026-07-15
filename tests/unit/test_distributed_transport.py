"""Unit tests for the transport + message codec."""
from __future__ import annotations

from typing import List
from uuid import uuid4

import pytest

from distributed.codec import JSONMessageCodec
from distributed.messages import (
    DeregisterMessage,
    ErrorMessage,
    HeartbeatMessage,
    Message,
    RegisterMessage,
    ResultMessage,
    StatusMessage,
    TaskMessage,
)
from distributed.transport import Channels, InMemoryTransport
from models.task import Task


@pytest.fixture()
def transport() -> InMemoryTransport:
    t = InMemoryTransport()
    t.start()
    yield t
    t.stop()


class TestCodec:
    def test_all_message_types_round_trip(self):
        codec = JSONMessageCodec()
        task = Task(description="x", required_capabilities=["code"])
        messages: List[Message] = [
            RegisterMessage(sender="w1", worker_id="w1", capabilities=["code"]),
            DeregisterMessage(sender="w1", worker_id="w1"),
            HeartbeatMessage(sender="w1", worker_id="w1", state="idle"),
            TaskMessage(sender="s", worker_id="w1", task=task),
            ResultMessage(sender="w1", worker_id="w1", task_id=task.id, success=True, output="done"),
            ErrorMessage(sender="w1", error="boom"),
            StatusMessage(sender="w1", worker_id="w1", state="idle"),
        ]
        for msg in messages:
            decoded = codec.decode(codec.encode(msg))
            assert type(decoded) is type(msg)
            assert decoded.type == msg.type
            assert decoded.sender == msg.sender

    def test_task_survives_round_trip(self):
        codec = JSONMessageCodec()
        task = Task(description="build", required_capabilities=["code"], dependencies=[uuid4()])
        msg = TaskMessage(sender="s", worker_id="w1", task=task)
        decoded = codec.decode(codec.encode(msg))
        assert decoded.task.id == task.id
        assert decoded.task.dependencies == task.dependencies


class TestTransport:
    def test_publish_delivers_to_subscribers(self, transport):
        received: List[Message] = []
        transport.subscribe(Channels.HEARTBEAT, received.append)
        transport.publish(Channels.HEARTBEAT, HeartbeatMessage(sender="w1", worker_id="w1"))
        assert len(received) == 1
        assert isinstance(received[0], HeartbeatMessage)

    def test_delivered_message_is_a_decoded_copy(self, transport):
        received: List[Message] = []
        transport.subscribe(Channels.REGISTER, received.append)
        original = RegisterMessage(sender="w1", worker_id="w1", capabilities=["code"])
        transport.publish(Channels.REGISTER, original)
        # Round-tripped through the codec → equal by value, not identity.
        assert received[0] is not original
        assert received[0].worker_id == "w1"

    def test_unsubscribe_stops_delivery(self, transport):
        received: List[Message] = []
        handler = received.append
        transport.subscribe(Channels.STATUS, handler)
        transport.unsubscribe(Channels.STATUS, handler)
        transport.publish(Channels.STATUS, StatusMessage(sender="w1", worker_id="w1", state="idle"))
        assert received == []

    def test_bad_handler_does_not_break_others(self, transport):
        good: List[Message] = []

        def boom(_m):
            raise RuntimeError("bad subscriber")

        transport.subscribe(Channels.HEARTBEAT, boom)
        transport.subscribe(Channels.HEARTBEAT, good.append)
        transport.publish(Channels.HEARTBEAT, HeartbeatMessage(sender="w1", worker_id="w1"))
        assert len(good) == 1

    def test_topic_isolation(self, transport):
        a: List[Message] = []
        transport.subscribe("topic.a", a.append)
        transport.publish("topic.b", HeartbeatMessage(sender="w1", worker_id="w1"))
        assert a == []
