"""Unit tests for the Tick iteration on the v0.7 graph runtime (ADR-0011)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List

import pytest

from kernel.context import KernelContext
from kernel.tick import Tick, TickResult
from models.task import Task
from runtime.lifecycle import WorkerState


class _Agent:
    capabilities: List[str] = ["code"]

    def initialize(self) -> None: ...
    def execute(self, task: Task) -> Any:
        return f"done: {task.description}"
    def heartbeat(self) -> datetime:
        return datetime.now(timezone.utc)
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def shutdown(self) -> None: ...


class _SickAgent(_Agent):
    """Healthy at registration, then stops answering heartbeats."""

    def __init__(self) -> None:
        self.sick = False

    def heartbeat(self) -> datetime:
        if self.sick:
            raise RuntimeError("no pulse")
        return datetime.now(timezone.utc)


@pytest.fixture()
def context() -> KernelContext:
    ctx = KernelContext.in_memory()
    yield ctx
    ctx.worker_runtime.shutdown()


@pytest.fixture()
def tick(context) -> Tick:
    return Tick(context)


class TestTickDispatch:
    def test_empty_tick_is_noop(self, tick):
        result = tick.run_once(1)
        assert isinstance(result, TickResult)
        assert result.dispatched == 0
        assert result.results == []

    def test_tick_dispatches_a_wave(self, context, tick):
        context.worker_runtime.register_worker(_Agent())
        for i in range(3):
            context.graph.add_task(
                Task(description=f"t{i}", required_capabilities=["code"])
            )

        result = tick.run_once(1)

        # Local backend frees the worker synchronously, so one tick drains all
        # three independent ready tasks.
        assert result.dispatched == 3
        assert len(result.results) == 3
        assert all(r.success for r in result.results)
        assert result.pending_after == 0
        assert result.active_workers == 1


class TestTickHealth:
    def test_unresponsive_worker_marked_failed(self, context, tick):
        agent = _SickAgent()
        wid = context.worker_runtime.register_worker(agent)
        agent.sick = True  # falls ill after registration

        result = tick.run_once(1)

        assert wid in result.aged_out
        assert context.worker_runtime.get_worker(wid).state == WorkerState.FAILED
        assert result.active_workers == 0

    def test_healthy_worker_survives(self, context, tick):
        wid = context.worker_runtime.register_worker(_Agent())
        result = tick.run_once(1)
        assert wid not in result.aged_out
        assert context.worker_runtime.get_worker(wid).state == WorkerState.IDLE
