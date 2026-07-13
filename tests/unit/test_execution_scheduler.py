"""Unit tests for the ExecutionScheduler (Sprint 7)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List

import pytest

from models.task import Task
from runtime import DefaultWorkerRuntime
from scheduling import ExecutionScheduler, MaxAttemptsRetryPolicy
from task_graph import InMemoryTaskGraph
from task_graph.state import NodeState


class OkWorker:
    capabilities: List[str] = ["code"]

    def initialize(self): ...
    def execute(self, task: Task) -> Any:
        return f"done:{task.description}"
    def heartbeat(self) -> datetime:
        return datetime.now(timezone.utc)
    def pause(self): ...
    def resume(self): ...
    def shutdown(self): ...


class BoomWorker(OkWorker):
    def execute(self, task: Task) -> Any:
        raise RuntimeError("boom")


def mk(desc, deps=None, caps=("code",)) -> Task:
    return Task(description=desc, dependencies=deps or [], required_capabilities=list(caps))


@pytest.fixture()
def graph() -> InMemoryTaskGraph:
    return InMemoryTaskGraph()


class TestScheduling:
    def test_runs_linear_dag_to_completion(self, graph):
        a = mk("a")
        b = mk("b", deps=[a.id])
        graph.add_task(a)
        graph.add_task(b)

        runtime = DefaultWorkerRuntime()
        runtime.register_worker(OkWorker())
        scheduler = ExecutionScheduler(graph, runtime)

        scheduler.run_until_idle()

        assert len(graph.completed_tasks()) == 2
        assert graph.has_active_work() is False
        runtime.shutdown()

    def test_task_with_no_capable_worker_is_left_ready(self, graph):
        graph.add_task(mk("gpu-task", caps=["gpu"]))
        runtime = DefaultWorkerRuntime()
        runtime.register_worker(OkWorker())  # only "code"
        scheduler = ExecutionScheduler(graph, runtime)

        scheduler.run_until_idle()

        assert len(graph.completed_tasks()) == 0
        assert len(graph.ready_tasks()) == 1  # still waiting, not failed
        runtime.shutdown()

    def test_retry_policy_bounds_attempts(self, graph):
        task = mk("flaky")
        graph.add_task(task)

        runtime = DefaultWorkerRuntime()
        wid = runtime.register_worker(BoomWorker())
        scheduler = ExecutionScheduler(
            graph, runtime, retry_policy=MaxAttemptsRetryPolicy(max_attempts=2)
        )

        scheduler.run_until_idle()

        # 2 attempts, then left FAILED (no infinite retry).
        assert graph.get_node(task.id).state == NodeState.FAILED
        assert runtime.worker_metrics(wid).tasks_executed == 2
        runtime.shutdown()

    def test_schedule_wave_returns_dispatch_count(self, graph):
        graph.add_task(mk("a"))
        graph.add_task(mk("b"))  # both independent, both ready
        runtime = DefaultWorkerRuntime()
        runtime.register_worker(OkWorker())
        runtime.register_worker(OkWorker())
        scheduler = ExecutionScheduler(graph, runtime)

        assert scheduler.schedule_wave() == 2  # one task per worker
        runtime.shutdown()
