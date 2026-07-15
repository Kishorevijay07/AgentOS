"""
Integration test — the Distributed Runtime end-to-end over InMemoryTransport.

Planner → Task Graph → DistributedScheduler → (transport) → RemoteWorkerNode(s).
The scheduler dispatches by capability and never touches a worker object; workers
run tasks received as messages and answer with result messages.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List

import pytest

from agents.coding import CodingAgent
from agents.documentation import DocumentationAgent
from agents.research import ResearchAgent
from agents.testing import TestingAgent
from distributed import (
    DistributedScheduler,
    InMemoryTransport,
    RemoteWorkerNode,
    WorkerDirectory,
)
from models.task import Task
from planning import TemplatePlanner
from planning.models import Goal
from scheduling import MaxAttemptsRetryPolicy
from task_graph import InMemoryTaskGraph, PlanGraphBuilder
from task_graph.state import NodeState


class FailingWorker:
    capabilities: List[str] = ["code"]

    def initialize(self): ...
    def execute(self, task: Task) -> Any:
        raise RuntimeError("remote boom")
    def heartbeat(self) -> datetime:
        return datetime.now(timezone.utc)
    def pause(self): ...
    def resume(self): ...
    def shutdown(self): ...


@pytest.fixture()
def cluster():
    transport = InMemoryTransport()
    transport.start()
    directory = WorkerDirectory(transport)
    directory.start()
    nodes: List[RemoteWorkerNode] = []

    def _spawn(worker, worker_id):
        node = RemoteWorkerNode(worker, transport, worker_id=worker_id)
        node.start(start_heartbeat=False)  # deterministic: no background beats
        nodes.append(node)
        return node

    yield transport, directory, _spawn

    for n in nodes:
        n.stop()
    transport.stop()


class TestDistributedExecution:
    def test_full_dag_runs_across_remote_workers(self, cluster):
        transport, directory, spawn = cluster
        for agent, wid in [
            (ResearchAgent(), "research-1"),
            (CodingAgent(), "coder-1"),
            (TestingAgent(), "tester-1"),
            (DocumentationAgent(), "doc-1"),
        ]:
            spawn(agent, wid)

        assert len(directory.available_workers()) == 4

        plan = TemplatePlanner().plan(Goal(description="Build a REST API for a blog"))
        graph = PlanGraphBuilder().build(plan)

        scheduler = DistributedScheduler(graph, directory, transport)
        scheduler.start()
        scheduler.run_until_idle()

        assert len(graph.completed_tasks()) == 5
        assert graph.has_active_work() is False

    def test_capability_routing_leaves_unplaceable_tasks(self, cluster):
        transport, directory, spawn = cluster
        spawn(ResearchAgent(), "research-1")  # only research

        plan = TemplatePlanner().plan(Goal(description="x"))
        graph = PlanGraphBuilder().build(plan)
        scheduler = DistributedScheduler(graph, directory, transport)
        scheduler.start()
        scheduler.run_until_idle()

        # Research steps done; code/test/doc can't be placed remotely → not run.
        assert 0 < len(graph.completed_tasks()) < 5

    def test_remote_failure_is_retried_then_failed(self, cluster):
        transport, directory, spawn = cluster
        spawn(FailingWorker(), "flaky-1")

        graph = InMemoryTaskGraph()
        task = Task(description="flaky", required_capabilities=["code"])
        graph.add_task(task)

        scheduler = DistributedScheduler(
            graph, directory, transport, retry_policy=MaxAttemptsRetryPolicy(max_attempts=2)
        )
        scheduler.start()
        scheduler.run_until_idle()

        assert graph.get_node(task.id).state == NodeState.FAILED
        assert task.retry_count == 2  # bounded retries, no infinite loop

    def test_scheduler_never_imports_worker_or_runtime_internals(self):
        """Boundary: the distributed scheduler talks abstractions only."""
        import ast
        import pathlib

        import distributed.scheduler as mod

        tree = ast.parse(pathlib.Path(mod.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        # It may know the runtime's *outcome* type and abstractions, but never a
        # concrete worker or the concrete runtime implementation class import.
        assert "runtime.runtime" not in imported
        assert "agents.coding" not in imported
