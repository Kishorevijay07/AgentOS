"""Unit tests for checkpoint models, stores, and graph snapshot/restore."""
from __future__ import annotations

from uuid import uuid4

import pytest

from checkpoint import Checkpoint, FileCheckpointStore, InMemoryCheckpointStore
from models.task import Task
from task_graph import InMemoryTaskGraph
from task_graph.state import NodeState


def _graph_with_progress() -> InMemoryTaskGraph:
    """a (completed) → b (ready) → c (blocked)."""
    g = InMemoryTaskGraph()
    a = Task(description="a", required_capabilities=["code"])
    b = Task(description="b", required_capabilities=["code"], dependencies=[a.id])
    c = Task(description="c", required_capabilities=["code"], dependencies=[b.id])
    for t in (a, b, c):
        g.add_task(t)
    g.mark_running(a.id, "w1")
    g.mark_completed(a.id)
    return g


class TestSnapshotRestore:
    def test_snapshot_is_a_deep_copy(self):
        g = _graph_with_progress()
        snap = g.snapshot()
        # Mutating the live graph must not change the snapshot.
        ready = g.ready_tasks()[0]
        g.mark_running(ready.task_id, "w1")
        g.mark_completed(ready.task_id)
        snap_states = {n.description: n.state for n in snap}
        assert snap_states["b"] == NodeState.READY  # snapshot unaffected

    def test_restore_reproduces_states(self):
        snap = _graph_with_progress().snapshot()
        g2 = InMemoryTaskGraph()
        g2.restore(snap)
        states = {n.description: n.state for n in g2.nodes()}
        assert states == {"a": NodeState.COMPLETED, "b": NodeState.READY, "c": NodeState.BLOCKED}

    def test_running_task_is_reset_to_ready_on_restore(self):
        g = InMemoryTaskGraph()
        t = Task(description="interrupted", required_capabilities=["code"])
        g.add_task(t)
        g.mark_running(t.id, "w1")  # in-flight at "crash"
        snap = g.snapshot()

        g2 = InMemoryTaskGraph()
        g2.restore(snap)
        node = g2.get_node(t.id)
        assert node.state == NodeState.READY          # re-run
        assert node.assigned_worker is None

    def test_running_task_with_incomplete_dep_resets_to_blocked(self):
        g = InMemoryTaskGraph()
        a = Task(description="a", required_capabilities=["code"])
        b = Task(description="b", required_capabilities=["code"], dependencies=[a.id])
        g.add_task(a)
        g.add_task(b)
        # Force b RUNNING while a is not complete (pathological pre-crash state).
        g.get_node(b.id).state = NodeState.RUNNING
        g2 = InMemoryTaskGraph()
        g2.restore(g.snapshot())
        assert g2.get_node(b.id).state == NodeState.BLOCKED


class TestCheckpointModel:
    def test_json_round_trip_preserves_graph(self):
        cp = Checkpoint(tick_count=7, replans_done=2, nodes=_graph_with_progress().snapshot())
        restored = Checkpoint.model_validate_json(cp.model_dump_json())
        assert restored.tick_count == 7
        assert restored.replans_done == 2
        assert {n.description for n in restored.nodes} == {"a", "b", "c"}
        # Dependencies (UUIDs) and children (sets) survive the round-trip.
        b = next(n for n in restored.nodes if n.description == "b")
        assert len(b.dependencies) == 1

    def test_summary(self):
        cp = Checkpoint(nodes=_graph_with_progress().snapshot())
        summary = cp.summary()
        assert summary["nodes"] == 3
        assert summary["states"]["completed"] == 1


class TestStores:
    def test_in_memory_store_round_trip(self):
        store = InMemoryCheckpointStore()
        assert store.load() is None
        cp = Checkpoint(tick_count=3, nodes=_graph_with_progress().snapshot())
        store.save(cp)
        loaded = store.load()
        assert loaded.tick_count == 3
        assert len(loaded.nodes) == 3

    def test_in_memory_store_is_decoupled_from_live_nodes(self):
        store = InMemoryCheckpointStore()
        g = _graph_with_progress()
        store.save(Checkpoint(nodes=g.snapshot()))
        # Draining the live graph must not change what was stored.
        n = g.ready_tasks()[0]
        g.mark_running(n.task_id, "w"); g.mark_completed(n.task_id)
        assert {x.state.value for x in store.load().nodes if x.description == "b"} == {"ready"}

    def test_file_store_save_load(self, tmp_path):
        path = tmp_path / "run.checkpoint.json"
        store = FileCheckpointStore(path)
        assert store.load() is None
        store.save(Checkpoint(tick_count=5, nodes=_graph_with_progress().snapshot()))
        assert path.exists()
        loaded = store.load()
        assert loaded.tick_count == 5
        assert len(loaded.nodes) == 3

    def test_file_store_overwrite_is_atomic_no_leftover_tmp(self, tmp_path):
        path = tmp_path / "run.checkpoint.json"
        store = FileCheckpointStore(path)
        store.save(Checkpoint(tick_count=1, nodes=[]))
        store.save(Checkpoint(tick_count=2, nodes=[]))
        assert store.load().tick_count == 2
        assert not (tmp_path / "run.checkpoint.json.tmp").exists()
