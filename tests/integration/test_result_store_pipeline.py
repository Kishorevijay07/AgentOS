"""
Integration tests — ResultStore wired into the full pipeline.

Tests the end-to-end flow:
  Dispatcher (on a KernelContext with a ResultStore) → agent.execute() →
  ExecutionRecord persisted with correct metadata, logs, and artifacts
  reachable by TaskID.
"""
from __future__ import annotations

from typing import Any, List

import pytest

from agents.base import BaseAgent
from agents.registry import AgentRegistry
from agents.worker import WorkerMixin
from events.bus import InMemoryEventBus
from kernel.context import KernelContext
from kernel.dispatcher import Dispatcher
from models.task import Task
from result_store import LogLevel, ResultStore
from task_queue.result_queue import ResultQueue
from task_queue.task_queue import TaskQueue


# ---------------------------------------------------------------------------
# Helpers — minimal agents
# ---------------------------------------------------------------------------

class _SuccessAgent(WorkerMixin, BaseAgent):
    capabilities: List[str] = ["code"]

    def execute(self, task: Task) -> Any:
        return f"done: {task.description}"


class _FailingAgent(WorkerMixin, BaseAgent):
    capabilities: List[str] = ["code"]

    def execute(self, task: Task) -> Any:
        raise RuntimeError("agent exploded")


class _ArtifactAgent(WorkerMixin, BaseAgent):
    """Agent that attaches an artifact via the store it receives."""
    capabilities: List[str] = ["code"]
    _store: ResultStore

    def set_store(self, store: ResultStore) -> None:
        self._store = store

    def execute(self, task: Task) -> Any:
        self._store.add_artifact(task.id, "output.md", "# Result", media_type="text/markdown")
        return "artifact attached"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def store() -> ResultStore:
    return ResultStore()


@pytest.fixture()
def bus() -> InMemoryEventBus:
    return InMemoryEventBus()


def _build_pipeline(agent, store, bus):
    """Return (dispatcher, task_queue, registry, agent_id)."""
    context = KernelContext.in_memory(
        event_bus=bus,
        registry=AgentRegistry(),
        task_queue=TaskQueue(),
        result_queue=ResultQueue(),
        result_store=store,
    )

    agent_id = context.registry.register(agent)
    agent._configure_worker(registry=context.registry, bus=bus, agent_id=agent_id)
    agent.initialize()

    dispatcher = Dispatcher(context)
    return dispatcher, context.task_queue, context.registry, agent_id


# ---------------------------------------------------------------------------
# Successful execution
# ---------------------------------------------------------------------------

class TestSuccessfulExecution:
    def test_record_created_for_task(self, store, bus):
        agent = _SuccessAgent()
        dispatcher, task_queue, _, _ = _build_pipeline(agent, store, bus)

        task = Task(description="Write code", required_capabilities=["code"])
        task_queue.add_task(task)
        dispatcher.dispatch_next()

        record = store.get(task.id)
        assert record is not None

    def test_record_is_closed_after_run(self, store, bus):
        agent = _SuccessAgent()
        dispatcher, task_queue, _, _ = _build_pipeline(agent, store, bus)

        task = Task(description="Write code", required_capabilities=["code"])
        task_queue.add_task(task)
        dispatcher.dispatch_next()

        record = store.get(task.id)
        assert record.is_open is False

    def test_record_success_is_true(self, store, bus):
        agent = _SuccessAgent()
        dispatcher, task_queue, _, _ = _build_pipeline(agent, store, bus)

        task = Task(description="Write code", required_capabilities=["code"])
        task_queue.add_task(task)
        dispatcher.dispatch_next()

        assert store.get(task.id).success is True

    def test_record_output_matches_agent_return(self, store, bus):
        agent = _SuccessAgent()
        dispatcher, task_queue, _, _ = _build_pipeline(agent, store, bus)

        task = Task(description="Build feature", required_capabilities=["code"])
        task_queue.add_task(task)
        dispatcher.dispatch_next()

        record = store.get(task.id)
        assert record.output == f"done: {task.description}"

    def test_record_agent_id_matches_registry(self, store, bus):
        agent = _SuccessAgent()
        dispatcher, task_queue, _, agent_id = _build_pipeline(agent, store, bus)

        task = Task(description="Do something", required_capabilities=["code"])
        task_queue.add_task(task)
        dispatcher.dispatch_next()

        assert store.get(task.id).agent_id == agent_id

    def test_duration_seconds_is_positive(self, store, bus):
        agent = _SuccessAgent()
        dispatcher, task_queue, _, _ = _build_pipeline(agent, store, bus)

        task = Task(description="Task", required_capabilities=["code"])
        task_queue.add_task(task)
        dispatcher.dispatch_next()

        assert store.get(task.id).duration_seconds is not None
        assert store.get(task.id).duration_seconds >= 0.0

    def test_dispatcher_logs_task_start_and_complete(self, store, bus):
        agent = _SuccessAgent()
        dispatcher, task_queue, _, _ = _build_pipeline(agent, store, bus)

        task = Task(description="Logged task", required_capabilities=["code"])
        task_queue.add_task(task)
        dispatcher.dispatch_next()

        logs = store.get(task.id).logs
        messages = [e.message for e in logs]
        assert any("started" in m.lower() for m in messages)
        assert any("completed" in m.lower() or "successfully" in m.lower() for m in messages)

    def test_dispatcher_log_source_is_dispatcher(self, store, bus):
        agent = _SuccessAgent()
        dispatcher, task_queue, _, _ = _build_pipeline(agent, store, bus)

        task = Task(description="Sourced task", required_capabilities=["code"])
        task_queue.add_task(task)
        dispatcher.dispatch_next()

        sources = {e.source for e in store.get(task.id).logs}
        assert "Dispatcher" in sources


# ---------------------------------------------------------------------------
# Failed execution
# ---------------------------------------------------------------------------

class TestFailedExecution:
    def test_record_success_is_false_on_exception(self, store, bus):
        agent = _FailingAgent()
        dispatcher, task_queue, _, _ = _build_pipeline(agent, store, bus)

        task = Task(description="Failing task", required_capabilities=["code"])
        task_queue.add_task(task)
        dispatcher.dispatch_next()

        assert store.get(task.id).success is False

    def test_record_error_field_populated(self, store, bus):
        agent = _FailingAgent()
        dispatcher, task_queue, _, _ = _build_pipeline(agent, store, bus)

        task = Task(description="Failing task", required_capabilities=["code"])
        task_queue.add_task(task)
        dispatcher.dispatch_next()

        record = store.get(task.id)
        assert record.error is not None
        assert "exploded" in record.error

    def test_error_logged_at_error_level(self, store, bus):
        agent = _FailingAgent()
        dispatcher, task_queue, _, _ = _build_pipeline(agent, store, bus)

        task = Task(description="Failing task", required_capabilities=["code"])
        task_queue.add_task(task)
        dispatcher.dispatch_next()

        error_logs = [e for e in store.get(task.id).logs if e.level == LogLevel.ERROR]
        assert len(error_logs) >= 1
        assert "exploded" in error_logs[0].message


# ---------------------------------------------------------------------------
# Artifact attachment from inside an agent
# ---------------------------------------------------------------------------

class TestArtifactAttachment:
    def test_artifact_reachable_by_task_id(self, store, bus):
        agent = _ArtifactAgent()
        agent.set_store(store)
        dispatcher, task_queue, _, _ = _build_pipeline(agent, store, bus)

        task = Task(description="Generate report", required_capabilities=["code"])
        task_queue.add_task(task)
        dispatcher.dispatch_next()

        record = store.get(task.id)
        assert len(record.artifacts) == 1
        assert record.artifacts[0].name == "output.md"
        assert record.artifacts[0].content == "# Result"
        assert record.artifacts[0].media_type == "text/markdown"


# ---------------------------------------------------------------------------
# Multiple tasks — independent records
# ---------------------------------------------------------------------------

class TestMultipleTaskRecords:
    def test_each_task_has_its_own_record(self, store, bus):
        context = KernelContext.in_memory(
            event_bus=bus,
            registry=AgentRegistry(),
            task_queue=TaskQueue(),
            result_queue=ResultQueue(),
            result_store=store,
        )

        a1, a2 = _SuccessAgent(), _SuccessAgent()
        id1 = context.registry.register(a1)
        id2 = context.registry.register(a2)
        for a, aid in [(a1, id1), (a2, id2)]:
            a._configure_worker(registry=context.registry, bus=bus, agent_id=aid)
            a.initialize()

        dispatcher = Dispatcher(context)

        t1 = Task(description="Task 1", required_capabilities=["code"])
        t2 = Task(description="Task 2", required_capabilities=["code"])
        context.task_queue.add_task(t1)
        context.task_queue.add_task(t2)

        dispatcher.dispatch_available()

        assert store.get(t1.id) is not None
        assert store.get(t2.id) is not None
        assert store.get(t1.id).task_id != store.get(t2.id).task_id
        assert len(store.successful()) == 2

    def test_dispatch_and_collect_results(self, store, bus):
        """A dispatched task produces a collectable result."""
        agent = _SuccessAgent()
        dispatcher, task_queue, _, _ = _build_pipeline(agent, store, bus)

        task = Task(description="Compat task", required_capabilities=["code"])
        task_queue.add_task(task)

        dispatched = dispatcher.dispatch_next()
        assert dispatched is True
        results = dispatcher.collect_results()
        assert len(results) == 1
        assert results[0].success is True
