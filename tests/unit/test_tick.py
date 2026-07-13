"""Unit tests for the Tick iteration and heartbeat aging (ADR-0009)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, List

import pytest

from agents.base import BaseAgent
from agents.worker import WorkerMixin
from kernel.context import KernelContext
from kernel.dispatcher import Dispatcher
from kernel.tick import Tick, TickResult
from models.enums import AgentStatus
from models.task import Task


class _Agent(WorkerMixin, BaseAgent):
    capabilities: List[str] = ["code"]

    def execute(self, task: Task) -> Any:
        return f"done: {task.description}"


def _wire(context: KernelContext, agent: BaseAgent) -> str:
    agent_id = context.registry.register(agent)
    agent._configure_worker(
        registry=context.registry, bus=context.event_bus, agent_id=agent_id
    )
    agent.initialize()
    return agent_id


@pytest.fixture()
def context() -> KernelContext:
    return KernelContext.in_memory()


@pytest.fixture()
def tick(context) -> Tick:
    return Tick(context, Dispatcher(context))


class TestTickDispatch:
    def test_empty_tick_is_noop(self, tick):
        result = tick.run_once(1)
        assert isinstance(result, TickResult)
        assert result.dispatched == 0
        assert result.results == []

    def test_tick_dispatches_a_wave(self, context, tick):
        _wire(context, _Agent())
        for i in range(3):
            context.task_queue.add_task(
                Task(description=f"t{i}", required_capabilities=["code"])
            )

        result = tick.run_once(1)

        assert result.dispatched == 3
        assert len(result.results) == 3
        assert result.pending_after == 0
        assert result.active_workers == 1


class TestHeartbeatAging:
    def test_stale_worker_marked_offline(self, context, tick):
        agent_id = _wire(context, _Agent())

        # Back-date the heartbeat well past the offline threshold.
        record = next(
            r for r in context.registry.list_agents() if r.agent_id == agent_id
        )
        stale = context.settings.agent_offline_after_seconds + 10
        record.last_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=stale)

        result = tick.run_once(1)

        assert agent_id in result.aged_out
        assert context.registry.get_status(agent_id) == AgentStatus.OFFLINE
        assert result.active_workers == 0

    def test_fresh_worker_not_aged_out(self, context, tick):
        agent_id = _wire(context, _Agent())
        result = tick.run_once(1)
        assert agent_id not in result.aged_out
        assert context.registry.get_status(agent_id) == AgentStatus.IDLE
