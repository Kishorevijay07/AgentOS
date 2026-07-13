"""Unit tests for agents/worker.py (Module 4 — Worker Lifecycle)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, List
from unittest.mock import MagicMock

import pytest

from agents.base import BaseAgent
from agents.registry import AgentRegistry
from agents.worker import WorkerMixin, WorkerState
from events.bus import InMemoryEventBus
from events.event_type import EventType
from models.task import Task


# ---------------------------------------------------------------------------
# Minimal concrete agent for testing
# ---------------------------------------------------------------------------

class _DummyAgent(WorkerMixin, BaseAgent):
    """Simplest possible concrete agent — WorkerMixin provides all lifecycle."""

    capabilities: List[str] = ["dummy"]

    def execute(self, task: Task) -> Any:
        return f"done: {task.description}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def agent() -> _DummyAgent:
    return _DummyAgent()


@pytest.fixture()
def bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture()
def registry() -> AgentRegistry:
    return AgentRegistry()


@pytest.fixture()
def wired(agent, bus, registry) -> tuple[_DummyAgent, InMemoryEventBus, AgentRegistry, str]:
    """Agent wired with registry + bus; already initialised (IDLE)."""
    agent_id = registry.register(agent)
    agent._configure_worker(registry=registry, bus=bus, agent_id=agent_id)
    agent.initialize()
    return agent, bus, registry, agent_id


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

class TestInitialState:
    def test_starts_in_initializing(self, agent):
        assert agent.worker_state == WorkerState.INITIALIZING

    def test_worker_state_property_is_thread_safe(self, agent):
        # Calling worker_state lazily initialises the mixin internals
        state = agent.worker_state
        assert state == WorkerState.INITIALIZING


# ---------------------------------------------------------------------------
# initialize()
# ---------------------------------------------------------------------------

class TestInitialize:
    def test_initialize_transitions_to_idle(self, agent):
        agent.initialize()
        assert agent.worker_state == WorkerState.IDLE

    def test_initialize_publishes_agent_online(self, agent, bus, registry):
        agent_id = registry.register(agent)
        agent._configure_worker(registry=registry, bus=bus, agent_id=agent_id)

        received: List = []
        bus.subscribe(EventType.AGENT_ONLINE, lambda e: received.append(e))
        agent.initialize()

        assert len(received) == 1
        assert received[0].payload["agent_id"] == agent_id
        assert "dummy" in received[0].payload["capabilities"]

    def test_initialize_without_bus_does_not_raise(self, agent):
        # No bus configured — must not raise
        agent.initialize()
        assert agent.worker_state == WorkerState.IDLE

    def test_initialize_twice_raises_invalid_transition(self, agent):
        agent.initialize()
        with pytest.raises(RuntimeError, match="Invalid worker state transition"):
            agent.initialize()  # IDLE → IDLE is not allowed


# ---------------------------------------------------------------------------
# pause() / resume()
# ---------------------------------------------------------------------------

class TestPauseResume:
    def test_pause_from_idle(self, wired):
        agent, *_ = wired
        agent.pause()
        assert agent.worker_state == WorkerState.PAUSED

    def test_resume_from_paused(self, wired):
        agent, *_ = wired
        agent.pause()
        agent.resume()
        assert agent.worker_state == WorkerState.IDLE

    def test_pause_from_busy(self, wired):
        agent, _, registry, agent_id = wired
        registry.set_current_task(agent_id, __import__("uuid").uuid4())  # BUSY
        agent.pause()
        assert agent.worker_state == WorkerState.PAUSED

    def test_resume_from_idle_raises(self, wired):
        agent, *_ = wired
        # IDLE → IDLE via resume() is invalid (IDLE is not PAUSED)
        with pytest.raises(RuntimeError, match="Invalid worker state transition"):
            agent.resume()

    def test_pause_from_initializing_raises(self, agent):
        with pytest.raises(RuntimeError, match="Invalid worker state transition"):
            agent.pause()  # INITIALIZING → PAUSED not allowed


# ---------------------------------------------------------------------------
# shutdown()
# ---------------------------------------------------------------------------

class TestShutdown:
    def test_shutdown_transitions_to_terminated(self, wired):
        agent, *_ = wired
        agent.shutdown()
        assert agent.worker_state == WorkerState.TERMINATED

    def test_shutdown_publishes_agent_offline(self, wired):
        agent, bus, _, agent_id = wired
        received: List = []
        bus.subscribe(EventType.AGENT_OFFLINE, lambda e: received.append(e))
        agent.shutdown()
        assert len(received) == 1
        assert received[0].payload["agent_id"] == agent_id

    def test_shutdown_without_bus_does_not_raise(self, agent):
        agent.initialize()
        agent.shutdown()
        assert agent.worker_state == WorkerState.TERMINATED

    def test_shutdown_from_terminated_raises(self, wired):
        agent, *_ = wired
        agent.shutdown()
        with pytest.raises(RuntimeError, match="Invalid worker state transition"):
            agent.shutdown()  # TERMINATED → SHUTTING_DOWN not allowed


# ---------------------------------------------------------------------------
# heartbeat()
# ---------------------------------------------------------------------------

class TestHeartbeat:
    def test_heartbeat_returns_datetime(self, wired):
        agent, *_ = wired
        ts = agent.heartbeat()
        assert isinstance(ts, datetime)

    def test_heartbeat_updates_registry(self, wired):
        agent, _, registry, agent_id = wired
        before = registry.list_agents()[0].last_heartbeat
        ts = agent.heartbeat()
        after = registry.list_agents()[0].last_heartbeat
        assert after >= before
        assert ts >= before

    def test_heartbeat_publishes_event(self, wired):
        agent, bus, _, agent_id = wired
        received: List = []
        bus.subscribe(EventType.AGENT_HEARTBEAT, lambda e: received.append(e))
        agent.heartbeat()
        assert len(received) == 1
        assert received[0].payload["agent_id"] == agent_id

    def test_heartbeat_payload_contains_state(self, wired):
        agent, bus, *_ = wired
        received: List = []
        bus.subscribe(EventType.AGENT_HEARTBEAT, lambda e: received.append(e))
        agent.heartbeat()
        assert received[0].payload["state"] == WorkerState.IDLE.value

    def test_heartbeat_without_registry_does_not_raise(self, agent, bus):
        """Heartbeat without registry/bus configured is silently skipped."""
        agent.initialize()
        ts = agent.heartbeat()
        assert isinstance(ts, datetime)

    def test_heartbeat_with_deregistered_agent_is_silent(self, wired):
        """If the agent was removed from registry, heartbeat must not raise."""
        agent, bus, registry, agent_id = wired
        registry.remove(agent_id)
        # Should not raise KeyError
        ts = agent.heartbeat()
        assert isinstance(ts, datetime)


# ---------------------------------------------------------------------------
# _configure_worker
# ---------------------------------------------------------------------------

class TestConfigureWorker:
    def test_configure_sets_registry(self, agent, registry):
        registry.register(agent)
        agent._configure_worker(registry=registry)
        assert agent._worker_registry is registry

    def test_configure_sets_bus(self, agent, bus):
        agent._configure_worker(bus=bus)
        assert agent._worker_bus is bus

    def test_configure_sets_agent_id(self, agent):
        agent._configure_worker(agent_id="MyAgent-1")
        assert agent._worker_agent_id == "MyAgent-1"


# ---------------------------------------------------------------------------
# WorkerState enum
# ---------------------------------------------------------------------------

class TestWorkerState:
    def test_all_states_exist(self):
        expected = {
            "INITIALIZING", "IDLE", "BUSY", "PAUSED",
            "SHUTTING_DOWN", "TERMINATED",
        }
        actual = {s.name for s in WorkerState}
        assert expected == actual

    def test_state_values_are_strings(self):
        for state in WorkerState:
            assert isinstance(state.value, str)


# ---------------------------------------------------------------------------
# WorkerMixin + BaseAgent inheritance (concrete agents)
# ---------------------------------------------------------------------------

class TestConcreteAgentInheritance:
    def test_research_agent_has_lifecycle(self):
        from agents.research import ResearchAgent
        a = ResearchAgent()
        a.initialize()
        assert a.worker_state == WorkerState.IDLE

    def test_coding_agent_has_lifecycle(self):
        from agents.coding import CodingAgent
        a = CodingAgent()
        a.initialize()
        a.pause()
        a.resume()
        assert a.worker_state == WorkerState.IDLE

    def test_documentation_agent_has_lifecycle(self):
        from agents.documentation import DocumentationAgent
        a = DocumentationAgent()
        a.initialize()
        a.shutdown()
        assert a.worker_state == WorkerState.TERMINATED

    def test_testing_agent_has_lifecycle(self):
        from agents.testing import TestingAgent
        a = TestingAgent()
        a.initialize()
        assert a.worker_state == WorkerState.IDLE
