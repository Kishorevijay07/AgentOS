"""Unit tests for the Worker Runtime (Sprint 6)."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, List

import pytest

from models.task import Task
from runtime import (
    DefaultWorkerRuntime,
    WorkerBusyError,
    WorkerNotFoundError,
    WorkerState,
)


# --- fake workers (satisfy the Worker Protocol structurally) --------------

class OkWorker:
    capabilities: List[str] = ["code"]

    def __init__(self) -> None:
        self.initialized = False
        self.shut = False

    def initialize(self) -> None:
        self.initialized = True

    def execute(self, task: Task) -> Any:
        return f"done:{task.description}"

    def heartbeat(self) -> datetime:
        return datetime.now(timezone.utc)

    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def shutdown(self) -> None:
        self.shut = True


class BoomWorker(OkWorker):
    def execute(self, task: Task) -> Any:
        raise RuntimeError("boom")


class SlowWorker(OkWorker):
    def execute(self, task: Task) -> Any:
        time.sleep(0.3)
        return "slow"


class SickHeartbeatWorker(OkWorker):
    def heartbeat(self) -> datetime:
        raise RuntimeError("no pulse")


class BadInitWorker(OkWorker):
    def initialize(self) -> None:
        raise RuntimeError("init fail")


@pytest.fixture()
def runtime() -> DefaultWorkerRuntime:
    rt = DefaultWorkerRuntime()
    yield rt
    rt.shutdown()


def _task() -> Task:
    return Task(description="x", required_capabilities=["code"])


class TestRegistration:
    def test_register_initializes_to_idle(self, runtime):
        w = OkWorker()
        wid = runtime.register_worker(w)
        assert w.initialized is True
        assert runtime.get_worker(wid).state == WorkerState.IDLE

    def test_failed_init_isolated_as_failed_state(self, runtime):
        wid = runtime.register_worker(BadInitWorker())
        assert runtime.get_worker(wid).state == WorkerState.FAILED

    def test_unregister_removes_worker(self, runtime):
        wid = runtime.register_worker(OkWorker())
        runtime.unregister_worker(wid)
        with pytest.raises(WorkerNotFoundError):
            runtime.get_worker(wid)

    def test_unknown_worker_raises(self, runtime):
        with pytest.raises(WorkerNotFoundError):
            runtime.execute_task("ghost", _task())


class TestExecution:
    def test_success_updates_metrics(self, runtime):
        wid = runtime.register_worker(OkWorker())
        outcome = runtime.execute_task(wid, _task())
        assert outcome.success and outcome.output == "done:x"
        assert runtime.get_worker(wid).state == WorkerState.IDLE
        m = runtime.worker_metrics(wid)
        assert m.tasks_executed == 1 and m.tasks_succeeded == 1
        assert m.average_execution_seconds >= 0.0

    def test_task_failure_is_isolated(self, runtime):
        wid = runtime.register_worker(BoomWorker())
        outcome = runtime.execute_task(wid, _task())
        assert outcome.success is False and "boom" in outcome.error
        # Worker stays healthy — a bad task must not crash the worker.
        assert runtime.get_worker(wid).state == WorkerState.IDLE
        assert runtime.worker_metrics(wid).tasks_failed == 1

    def test_timeout_marks_worker_failed(self, runtime):
        wid = runtime.register_worker(SlowWorker())
        outcome = runtime.execute_task(wid, _task(), timeout=0.05)
        assert outcome.timed_out is True and outcome.success is False
        assert runtime.get_worker(wid).state == WorkerState.FAILED
        assert runtime.worker_metrics(wid).tasks_timed_out == 1

    def test_busy_worker_rejects_dispatch(self, runtime):
        wid = runtime.register_worker(OkWorker())
        handle = runtime.get_worker(wid)
        with handle.lock:
            handle.state = WorkerState.BUSY
        with pytest.raises(WorkerBusyError):
            runtime.execute_task(wid, _task())


class TestViewsAndHealth:
    def test_available_workers_excludes_paused(self, runtime):
        a = runtime.register_worker(OkWorker())
        b = runtime.register_worker(OkWorker())
        runtime.pause_worker(b)
        available = {h.worker_id for h in runtime.available_workers()}
        assert a in available and b not in available

    def test_pause_resume(self, runtime):
        wid = runtime.register_worker(OkWorker())
        runtime.pause_worker(wid)
        assert runtime.get_worker(wid).state == WorkerState.PAUSED
        runtime.resume_worker(wid)
        assert runtime.get_worker(wid).state == WorkerState.IDLE

    def test_health_check_fails_unresponsive_worker(self, runtime):
        wid = runtime.register_worker(SickHeartbeatWorker())
        failed = runtime.health_check()
        assert wid in failed
        assert runtime.get_worker(wid).state == WorkerState.FAILED

    def test_worker_status_snapshot(self, runtime):
        wid = runtime.register_worker(OkWorker())
        status = runtime.worker_status(wid)
        assert status["worker_id"] == wid
        assert status["state"] == "idle"
        assert status["capabilities"] == ["code"]


class TestShutdown:
    def test_shutdown_takes_all_offline(self):
        rt = DefaultWorkerRuntime()
        w = OkWorker()
        rt.register_worker(w)
        rt.shutdown()
        assert w.shut is True
        assert all(h.state == WorkerState.OFFLINE for h in rt.all_workers())
