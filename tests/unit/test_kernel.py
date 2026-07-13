"""
Unit tests for the Kernel composition root (ADR-0001 / ADR-0008).

Exercises the full wired runtime: register → submit → run → trace → shutdown,
and confirms the dependency-injection swap seam.
"""
from __future__ import annotations

from typing import Any, List

import pytest

from agents.base import BaseAgent
from agents.coding import CodingAgent
from agents.worker import WorkerMixin, WorkerState
from config.settings import KernelSettings
from events.bus import InMemoryEventBus
from events.event_type import EventType
from kernel import Kernel, KernelContext, KernelState, build_kernel
from models.enums import AgentStatus, Priority
from models.task import Task


class _CountingAgent(WorkerMixin, BaseAgent):
    capabilities: List[str] = ["code"]

    def __init__(self) -> None:
        self.count = 0

    def execute(self, task: Task) -> Any:
        self.count += 1
        return f"ran#{self.count}: {task.description}"


@pytest.fixture()
def kernel() -> Kernel:
    return build_kernel().boot()


class TestKernelWiring:
    def test_build_kernel_returns_wired_kernel(self, kernel):
        assert isinstance(kernel, Kernel)
        assert isinstance(kernel.settings, KernelSettings)

    def test_register_agent_brings_it_online(self, kernel):
        agent_id = kernel.register_agent(CodingAgent())
        assert kernel.registry.get_status(agent_id) == AgentStatus.IDLE
        online = [e for e in kernel.bus.history() if e.type == EventType.AGENT_ONLINE]
        assert any(e.payload["agent_id"] == agent_id for e in online)


class TestKernelExecution:
    def test_submit_and_run_single_task(self, kernel):
        agent_id = kernel.register_agent(CodingAgent())
        task = Task(description="Implement X", required_capabilities=["code"])
        kernel.submit(task)

        results = kernel.run_until_empty()

        assert len(results) == 1
        assert results[0].success is True

        # Agent released back to IDLE after execution.
        assert kernel.registry.get_status(agent_id) == AgentStatus.IDLE

        # TASK_CREATED (Kernel) and TASK_ASSIGNED (Scheduler) both fired.
        types = [e.type for e in kernel.bus.history()]
        assert EventType.TASK_CREATED in types
        assert EventType.TASK_ASSIGNED in types

        # ResultStore has a closed record with a non-negative duration.
        record = kernel.store.get(task.id)
        assert record is not None
        assert record.success is True
        assert record.is_open is False
        assert record.duration_seconds is not None and record.duration_seconds >= 0.0

    def test_single_agent_processes_multiple_same_capability_tasks(self, kernel):
        """Regression: the worker must return to IDLE so it can be reused."""
        agent = _CountingAgent()
        kernel.register_agent(agent)

        for i in range(3):
            kernel.submit(Task(description=f"task {i}", required_capabilities=["code"]))

        results = kernel.run_until_empty()

        assert len(results) == 3
        assert all(r.success for r in results)
        assert agent.count == 3

    def test_priority_orders_execution(self, kernel):
        agent = _CountingAgent()
        kernel.register_agent(agent)

        low = Task(description="low", required_capabilities=["code"], priority=Priority.LOW)
        crit = Task(description="crit", required_capabilities=["code"], priority=Priority.CRITICAL)
        kernel.submit(low)
        kernel.submit(crit)

        results = kernel.run_until_empty()
        # Critical must be executed before low.
        assert "crit" in str(results[0].output)


class TestKernelLifecycle:
    def test_shutdown_terminates_agents_and_fires_offline(self, kernel):
        offline: List[str] = []
        kernel.bus.subscribe(
            EventType.AGENT_OFFLINE, lambda e: offline.append(e.payload["agent_id"])
        )
        agent = CodingAgent()
        agent_id = kernel.register_agent(agent)

        kernel.shutdown()

        assert agent.worker_state == WorkerState.TERMINATED
        assert agent_id in offline


class TestKernelDependencyInjection:
    def test_injected_event_bus_is_used_unchanged(self):
        """Program-to-abstractions: inject one subsystem, everything else default."""
        my_bus = InMemoryEventBus()
        context = KernelContext.in_memory(event_bus=my_bus)
        kernel = Kernel(context).boot()
        kernel.register_agent(CodingAgent())
        kernel.submit(Task(description="X", required_capabilities=["code"]))
        kernel.run_until_idle()

        # Events landed on the injected bus, proving the seam wired through
        # Scheduler, Dispatcher, and workers without any naming a concrete bus.
        assert len(my_bus.history()) > 0
        assert kernel.bus is my_bus


class TestKernelRuntimeLifecycle:
    def test_state_transitions_boot_pause_resume_stop(self, kernel):
        # build_kernel().boot() → RUNNING (fixture already booted).
        assert kernel.state == KernelState.RUNNING
        kernel.pause()
        assert kernel.state == KernelState.PAUSED
        kernel.resume()
        assert kernel.state == KernelState.RUNNING
        kernel.stop()
        assert kernel.state == KernelState.STOPPED

    def test_tick_after_stop_raises(self, kernel):
        kernel.stop()
        with pytest.raises(RuntimeError):
            kernel.tick()

    def test_threaded_run_drains_queue_then_stop(self, kernel):
        kernel.register_agent(_CountingAgent())
        for i in range(5):
            kernel.submit(Task(description=f"t{i}", required_capabilities=["code"]))

        kernel.run()
        # Wait (bounded) for the background loop to drain the queue.
        import time

        deadline = time.time() + 3.0
        while not kernel.task_queue.is_empty() and time.time() < deadline:
            time.sleep(0.01)
        kernel.stop()

        assert kernel.task_queue.is_empty()
        assert kernel.state == KernelState.STOPPED
        assert len(kernel.store.successful()) == 5

    def test_health_snapshot_shape(self, kernel):
        kernel.register_agent(CodingAgent())
        health = kernel.health()
        assert health["state"] == KernelState.RUNNING.value
        assert health["workers"]["total"] == 1
        assert health["workers"]["idle"] == 1
        assert "pending" in health
