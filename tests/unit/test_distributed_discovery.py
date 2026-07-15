"""Unit tests for WorkerDirectory (discovery) and HeartbeatMonitor."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from distributed.discovery import WorkerDirectory, WorkerPresence
from distributed.heartbeat import HeartbeatEmitter, HeartbeatMonitor
from distributed.messages import DeregisterMessage, RegisterMessage
from distributed.transport import Channels, InMemoryTransport


@pytest.fixture()
def transport() -> InMemoryTransport:
    t = InMemoryTransport()
    t.start()
    yield t
    t.stop()


@pytest.fixture()
def directory(transport) -> WorkerDirectory:
    d = WorkerDirectory(transport)
    d.start()
    return d


class TestDiscovery:
    def test_register_adds_worker(self, transport, directory):
        transport.publish(
            Channels.REGISTER,
            RegisterMessage(sender="w1", worker_id="w1", capabilities=["code"]),
        )
        assert [w.worker_id for w in directory.available_workers()] == ["w1"]
        assert directory.get("w1").capabilities == ["code"]

    def test_deregister_removes_worker(self, transport, directory):
        transport.publish(Channels.REGISTER, RegisterMessage(sender="w1", worker_id="w1"))
        transport.publish(Channels.DEREGISTER, DeregisterMessage(sender="w1", worker_id="w1"))
        assert directory.get("w1") is None

    def test_heartbeat_refreshes_presence(self, transport, directory):
        transport.publish(Channels.REGISTER, RegisterMessage(sender="w1", worker_id="w1"))
        directory.mark_offline("w1")
        assert directory.get("w1").presence == WorkerPresence.OFFLINE
        # A heartbeat brings it back online.
        HeartbeatEmitter(transport, "w1").beat_once()
        assert directory.get("w1").presence == WorkerPresence.ONLINE

    def test_heartbeat_from_unknown_worker_ignored(self, transport, directory):
        HeartbeatEmitter(transport, "ghost").beat_once()
        assert directory.get("ghost") is None


class TestHeartbeatMonitor:
    def test_stale_worker_marked_offline(self, transport, directory):
        transport.publish(Channels.REGISTER, RegisterMessage(sender="w1", worker_id="w1"))
        monitor = HeartbeatMonitor(directory, timeout=10.0)

        future = datetime.now(timezone.utc) + timedelta(seconds=60)
        affected = monitor.check(now=future)

        assert "w1" in affected
        assert directory.get("w1").presence == WorkerPresence.OFFLINE

    def test_evicting_monitor_removes_worker(self, transport, directory):
        transport.publish(Channels.REGISTER, RegisterMessage(sender="w1", worker_id="w1"))
        monitor = HeartbeatMonitor(directory, timeout=10.0, evict=True)
        monitor.check(now=datetime.now(timezone.utc) + timedelta(seconds=60))
        assert directory.get("w1") is None

    def test_fresh_worker_survives(self, transport, directory):
        transport.publish(Channels.REGISTER, RegisterMessage(sender="w1", worker_id="w1"))
        monitor = HeartbeatMonitor(directory, timeout=10.0)
        assert monitor.check() == []
