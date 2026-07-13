"""Unit tests for the Dispatcher — execution, failure, worker release, events."""
from __future__ import annotations

from typing import Any, List

import pytest

from agents.base import BaseAgent
from agents.worker import WorkerMixin
from events.event import Event
from events.event_type import EventType
from kernel.context import KernelContext
from kernel.dispatcher import Dispatcher
from models.enums import AgentStatus
from models.task import Task


class _OkAgent(WorkerMixin, BaseAgent):
    capabilities: List[str] = ["code"]

    def execute(self, task: Task) -> Any:
        return f"ok: {task.description}"


class _BoomAgent(WorkerMixin, BaseAgent):
    capabilities: List[str] = ["code"]

    def execute(self, task: Task) -> Any:
        raise RuntimeError("kaboom")


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
def dispatcher(context) -> Dispatcher:
    return Dispatcher(context)


class TestExecution:
    def test_success_traced_and_released(self, context, dispatcher):
        agent_id = _wire(context, _OkAgent())
        task = Task(description="do it", required_capabilities=["code"])
        context.task_queue.add_task(task)

        assert dispatcher.dispatch_next() is True

        record = context.result_store.get(task.id)
        assert record.success is True
        # Worker returned to IDLE.
        assert context.registry.get_status(agent_id) == AgentStatus.IDLE

    def test_failure_traced(self, context, dispatcher):
        _wire(context, _BoomAgent())
        task = Task(description="fail", required_capabilities=["code"])
        context.task_queue.add_task(task)

        dispatcher.dispatch_next()

        record = context.result_store.get(task.id)
        assert record.success is False
        assert "kaboom" in record.error

    def test_no_worker_fails_task(self, context, dispatcher):
        task = Task(description="orphan", required_capabilities=["code"])
        context.task_queue.add_task(task)
        assert dispatcher.dispatch_next() is False
        assert task.id in {t.id for t in context.task_queue.failed_tasks()}


class TestLifecycleEvents:
    def _collect(self, context) -> List[Event]:
        events: List[Event] = []
        for et in EventType:
            context.event_bus.subscribe(et, lambda e: events.append(e))
        return events

    def test_started_and_completed_published(self, context, dispatcher):
        _wire(context, _OkAgent())
        events = self._collect(context)
        context.task_queue.add_task(
            Task(description="x", required_capabilities=["code"])
        )
        dispatcher.dispatch_next()

        types = [e.type for e in events]
        assert EventType.TASK_STARTED in types
        assert EventType.TASK_COMPLETED in types
        # execution_id is carried on the lifecycle events.
        started = next(e for e in events if e.type == EventType.TASK_STARTED)
        assert "execution_id" in started.payload

    def test_failed_event_published(self, context, dispatcher):
        _wire(context, _BoomAgent())
        events = self._collect(context)
        context.task_queue.add_task(
            Task(description="x", required_capabilities=["code"])
        )
        dispatcher.dispatch_next()

        types = [e.type for e in events]
        assert EventType.TASK_FAILED in types
        assert EventType.TASK_COMPLETED not in types


class TestWaveDispatch:
    def test_dispatch_available_drains_pending(self, context, dispatcher):
        _wire(context, _OkAgent())
        for i in range(4):
            context.task_queue.add_task(
                Task(description=f"t{i}", required_capabilities=["code"])
            )
        assert dispatcher.dispatch_available() == 4
        assert context.task_queue.is_empty()
