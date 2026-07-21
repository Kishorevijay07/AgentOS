"""
Integration tests — ResultStore wired into the full pipeline.

Tests the end-to-end flow on the v0.7 unified runtime:
  ExecutionScheduler (graph + worker runtime + ResultStore) → agent.execute() →
  ExecutionRecord persisted with correct metadata, logs, and artifacts
  reachable by TaskID.
"""
from __future__ import annotations

from typing import Any, List

import pytest

from agents.base import BaseAgent
from agents.worker import WorkerMixin
from events.bus import InMemoryEventBus
from models.task import Task
from result_store import LogLevel, ResultStore
from runtime import DefaultWorkerRuntime
from scheduling import ExecutionScheduler
from task_graph import InMemoryTaskGraph


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
    """Return (scheduler, graph, runtime, agent_id)."""
    graph = InMemoryTaskGraph()
    runtime = DefaultWorkerRuntime(event_bus=bus)
    agent_id = runtime.register_worker(agent)
    scheduler = ExecutionScheduler(graph, runtime, event_bus=bus, result_store=store)
    return scheduler, graph, runtime, agent_id


# ---------------------------------------------------------------------------
# Successful execution
# ---------------------------------------------------------------------------

class TestSuccessfulExecution:
    def test_record_created_for_task(self, store, bus):
        scheduler, graph, _, _ = _build_pipeline(_SuccessAgent(), store, bus)

        task = Task(description="Write code", required_capabilities=["code"])
        graph.add_task(task)
        scheduler.schedule_wave()

        record = store.get(task.id)
        assert record is not None

    def test_record_is_closed_after_run(self, store, bus):
        scheduler, graph, _, _ = _build_pipeline(_SuccessAgent(), store, bus)

        task = Task(description="Write code", required_capabilities=["code"])
        graph.add_task(task)
        scheduler.schedule_wave()

        record = store.get(task.id)
        assert record.is_open is False

    def test_record_success_is_true(self, store, bus):
        scheduler, graph, _, _ = _build_pipeline(_SuccessAgent(), store, bus)

        task = Task(description="Write code", required_capabilities=["code"])
        graph.add_task(task)
        scheduler.schedule_wave()

        assert store.get(task.id).success is True

    def test_record_output_matches_agent_return(self, store, bus):
        scheduler, graph, _, _ = _build_pipeline(_SuccessAgent(), store, bus)

        task = Task(description="Build feature", required_capabilities=["code"])
        graph.add_task(task)
        scheduler.schedule_wave()

        record = store.get(task.id)
        assert record.output == f"done: {task.description}"

    def test_record_agent_id_matches_runtime(self, store, bus):
        scheduler, graph, _, agent_id = _build_pipeline(_SuccessAgent(), store, bus)

        task = Task(description="Do something", required_capabilities=["code"])
        graph.add_task(task)
        scheduler.schedule_wave()

        assert store.get(task.id).agent_id == agent_id

    def test_duration_seconds_is_positive(self, store, bus):
        scheduler, graph, _, _ = _build_pipeline(_SuccessAgent(), store, bus)

        task = Task(description="Task", required_capabilities=["code"])
        graph.add_task(task)
        scheduler.schedule_wave()

        assert store.get(task.id).duration_seconds is not None
        assert store.get(task.id).duration_seconds >= 0.0

    def test_scheduler_logs_task_start_and_complete(self, store, bus):
        scheduler, graph, _, _ = _build_pipeline(_SuccessAgent(), store, bus)

        task = Task(description="Logged task", required_capabilities=["code"])
        graph.add_task(task)
        scheduler.schedule_wave()

        logs = store.get(task.id).logs
        messages = [e.message for e in logs]
        assert any("started" in m.lower() for m in messages)
        assert any("completed" in m.lower() or "successfully" in m.lower() for m in messages)

    def test_scheduler_log_source_is_scheduler(self, store, bus):
        scheduler, graph, _, _ = _build_pipeline(_SuccessAgent(), store, bus)

        task = Task(description="Sourced task", required_capabilities=["code"])
        graph.add_task(task)
        scheduler.schedule_wave()

        sources = {e.source for e in store.get(task.id).logs}
        assert "Scheduler" in sources


# ---------------------------------------------------------------------------
# Failed execution
# ---------------------------------------------------------------------------

class TestFailedExecution:
    def test_record_success_is_false_on_exception(self, store, bus):
        scheduler, graph, _, _ = _build_pipeline(_FailingAgent(), store, bus)

        task = Task(description="Failing task", required_capabilities=["code"])
        graph.add_task(task)
        scheduler.schedule_wave()

        assert store.get(task.id).success is False

    def test_record_error_field_populated(self, store, bus):
        scheduler, graph, _, _ = _build_pipeline(_FailingAgent(), store, bus)

        task = Task(description="Failing task", required_capabilities=["code"])
        graph.add_task(task)
        scheduler.schedule_wave()

        record = store.get(task.id)
        assert record.error is not None
        assert "exploded" in record.error

    def test_error_logged_at_error_level(self, store, bus):
        scheduler, graph, _, _ = _build_pipeline(_FailingAgent(), store, bus)

        task = Task(description="Failing task", required_capabilities=["code"])
        graph.add_task(task)
        scheduler.schedule_wave()

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
        scheduler, graph, _, _ = _build_pipeline(agent, store, bus)

        task = Task(description="Generate report", required_capabilities=["code"])
        graph.add_task(task)
        scheduler.schedule_wave()

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
        graph = InMemoryTaskGraph()
        runtime = DefaultWorkerRuntime(event_bus=bus)
        runtime.register_worker(_SuccessAgent())
        runtime.register_worker(_SuccessAgent())
        scheduler = ExecutionScheduler(graph, runtime, event_bus=bus, result_store=store)

        t1 = Task(description="Task 1", required_capabilities=["code"])
        t2 = Task(description="Task 2", required_capabilities=["code"])
        graph.add_task(t1)
        graph.add_task(t2)

        scheduler.run_until_idle()

        assert store.get(t1.id) is not None
        assert store.get(t2.id) is not None
        assert store.get(t1.id).task_id != store.get(t2.id).task_id
        assert len(store.successful()) == 2

    def test_store_not_required(self, bus):
        """The scheduler works without a ResultStore; outcomes still flow."""
        graph = InMemoryTaskGraph()
        runtime = DefaultWorkerRuntime(event_bus=bus)
        runtime.register_worker(_SuccessAgent())
        scheduler = ExecutionScheduler(graph, runtime, event_bus=bus)  # no store

        task = Task(description="Compat task", required_capabilities=["code"])
        graph.add_task(task)

        dispatched = scheduler.schedule_wave()
        assert dispatched == 1
        results = scheduler.drain_outcomes()
        assert len(results) == 1
        assert results[0].success is True
