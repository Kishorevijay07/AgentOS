"""Unit tests for RedisCheckpointStore — fake Redis client, no server."""
from __future__ import annotations

from typing import Dict, Optional

from checkpoint import Checkpoint, RedisCheckpointStore
from models.task import Task
from task_graph import InMemoryTaskGraph


class _FakeRedis:
    """Minimal redis stand-in: GET/SET on a dict."""

    def __init__(self) -> None:
        self._data: Dict[str, str] = {}

    def set(self, key: str, value: str) -> None:
        self._data[key] = value

    def get(self, key: str) -> Optional[str]:
        return self._data.get(key)


def _checkpoint() -> Checkpoint:
    g = InMemoryTaskGraph()
    a = Task(description="a", required_capabilities=["code"])
    b = Task(description="b", required_capabilities=["code"], dependencies=[a.id])
    g.add_task(a)
    g.add_task(b)
    g.mark_running(a.id, "w1")
    g.mark_completed(a.id)
    return Checkpoint(tick_count=3, replans_done=1, nodes=g.snapshot())


class TestRedisCheckpointStore:
    def test_load_none_when_empty(self):
        store = RedisCheckpointStore(client=_FakeRedis())
        assert store.load() is None

    def test_save_then_load_round_trip(self):
        client = _FakeRedis()
        store = RedisCheckpointStore(client=client, key="agentos:test")
        store.save(_checkpoint())

        # Stored as JSON under the configured key.
        assert "agentos:test" in client._data
        assert '"tick_count":3' in client._data["agentos:test"].replace(" ", "")

        loaded = store.load()
        assert loaded.tick_count == 3
        assert loaded.replans_done == 1
        assert {n.description for n in loaded.nodes} == {"a", "b"}

    def test_decodes_bytes_payload(self):
        class _BytesRedis(_FakeRedis):
            def get(self, key):
                v = self._data.get(key)
                return v.encode("utf-8") if v is not None else None

        store = RedisCheckpointStore(client=_BytesRedis())
        store.save(_checkpoint())
        assert store.load().tick_count == 3
