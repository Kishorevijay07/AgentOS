"""
Integration test — crash-and-resume through the Kernel.

Runs a DAG partway, checkpoints, throws the kernel away, builds a fresh kernel
with fresh workers, restores the checkpoint, and finishes — proving a run
survives a crash and resumes exactly where it left off without re-doing
completed work.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List

from checkpoint import FileCheckpointStore, InMemoryCheckpointStore
from kernel import Kernel, KernelContext, build_kernel
from models.task import Task
from reflection.reflector import HeuristicReflector
from task_graph.state import NodeState


class _Worker:
    capabilities: List[str] = ["code"]

    def __init__(self) -> None:
        self.ran: List[str] = []

    def initialize(self) -> None: ...
    def execute(self, task: Task) -> Any:
        self.ran.append(task.description)
        return f"a thorough answer for {task.description}"
    def heartbeat(self) -> datetime:
        return datetime.now(timezone.utc)
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def shutdown(self) -> None: ...


def _linear_dag(kernel: Kernel) -> List[Task]:
    a = Task(description="a", required_capabilities=["code"])
    b = Task(description="b", required_capabilities=["code"], dependencies=[a.id])
    c = Task(description="c", required_capabilities=["code"], dependencies=[b.id])
    for t in (a, b, c):
        kernel.submit(t)
    return [a, b, c]


class TestCrashAndResume:
    def test_resume_finishes_without_redoing_work(self):
        store = InMemoryCheckpointStore()

        # --- Run 1: do one tick (completes 'a'), then checkpoint and "crash". ---
        w1 = _Worker()
        k1 = build_kernel().boot()
        k1.register_agent(w1)
        tasks = _linear_dag(k1)
        k1.tick()                      # 'a' runs; 'b' unlocks
        k1.save_checkpoint(store)
        assert w1.ran == ["a"]
        assert k1.graph.get_node(tasks[0].id).state == NodeState.COMPLETED
        k1.shutdown()                  # process dies

        # --- Run 2: fresh kernel + fresh worker, restore, finish. ---
        w2 = _Worker()
        k2 = build_kernel().boot()
        k2.register_agent(w2)
        assert k2.load_checkpoint(store) is True

        outcomes = k2.run_until_idle()

        # The new worker only ran the remaining tasks — 'a' was not redone.
        assert w2.ran == ["b", "c"]
        assert len(outcomes) == 2
        assert len(k2.graph.completed_tasks()) == 3
        assert k2.graph.has_active_work() is False
        k2.shutdown()

    def test_load_returns_false_when_no_checkpoint(self):
        kernel = build_kernel().boot()
        assert kernel.load_checkpoint(InMemoryCheckpointStore()) is False
        kernel.shutdown()

    def test_reflection_budget_survives_resume(self):
        store = InMemoryCheckpointStore()

        # Run with reflection; poor output triggers replans, then checkpoint.
        class _PoorWorker(_Worker):
            def execute(self, task):
                self.ran.append(task.description)
                return "no"  # below the heuristic bar → replan

        ctx = KernelContext.in_memory(reflector=HeuristicReflector(),
                                      allowed_capabilities=["code"], max_replans=2)
        k1 = Kernel(ctx).boot()
        k1.register_agent(_PoorWorker())
        k1.submit(Task(description="task", required_capabilities=["code"]))
        k1.tick()                      # runs + reflects → 1 replan used
        assert ctx.reflection.replans_done == 1
        k1.save_checkpoint(store)
        k1.shutdown()

        # Resume: the spent budget carries over (cannot exceed max_replans).
        ctx2 = KernelContext.in_memory(reflector=HeuristicReflector(),
                                       allowed_capabilities=["code"], max_replans=2)
        k2 = Kernel(ctx2).boot()
        k2.register_agent(_PoorWorker())
        k2.load_checkpoint(store)
        assert ctx2.reflection.replans_done == 1  # budget restored
        k2.run_until_idle()
        # Total replans across the crash boundary never exceeds the cap.
        assert ctx2.reflection.replans_done <= 2
        k2.shutdown()


class TestAutoCheckpoint:
    def test_auto_checkpoint_every_tick(self, tmp_path):
        from config.settings import KernelSettings

        path = tmp_path / "auto.json"
        store = FileCheckpointStore(path)
        ctx = KernelContext.in_memory(
            KernelSettings(checkpoint_every_ticks=1), checkpoint_store=store
        )
        kernel = Kernel(ctx).boot()
        kernel.register_agent(_Worker())
        kernel.submit(Task(description="a", required_capabilities=["code"]))
        kernel.run_until_idle()

        assert path.exists()
        assert store.load() is not None  # a checkpoint was auto-written
        kernel.shutdown()
