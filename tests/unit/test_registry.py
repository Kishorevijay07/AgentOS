"""Unit tests for agents/registry.py (Module 1 — Agent Registry)."""
from __future__ import annotations

import pytest

from agents.coding import CodingAgent
from agents.registry import AgentRecord, AgentRegistry
from agents.research import ResearchAgent
from agents.testing import TestingAgent
from models.enums import AgentStatus
from models.task import Task


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def registry() -> AgentRegistry:
    return AgentRegistry()


@pytest.fixture()
def coder() -> CodingAgent:
    return CodingAgent()


@pytest.fixture()
def researcher() -> ResearchAgent:
    return ResearchAgent()


# ---------------------------------------------------------------------------
# register()
# ---------------------------------------------------------------------------

class TestRegister:
    def test_register_returns_human_readable_id(self, registry, coder):
        agent_id = registry.register(coder)
        assert agent_id.startswith("CodingAgent-")

    def test_register_increments_counter_per_class(self, registry):
        a1 = registry.register(CodingAgent())
        a2 = registry.register(CodingAgent())
        assert a1 == "CodingAgent-1"
        assert a2 == "CodingAgent-2"

    def test_register_different_classes_independent_counters(self, registry):
        c_id = registry.register(CodingAgent())
        r_id = registry.register(ResearchAgent())
        assert c_id == "CodingAgent-1"
        assert r_id == "ResearchAgent-1"

    def test_register_same_instance_twice_raises(self, registry, coder):
        registry.register(coder)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(coder)

    def test_registry_len_after_register(self, registry, coder, researcher):
        registry.register(coder)
        registry.register(researcher)
        assert len(registry) == 2


# ---------------------------------------------------------------------------
# remove()
# ---------------------------------------------------------------------------

class TestRemove:
    def test_remove_decrements_len(self, registry, coder):
        agent_id = registry.register(coder)
        registry.remove(agent_id)
        assert len(registry) == 0

    def test_remove_unknown_id_raises(self, registry):
        with pytest.raises(KeyError):
            registry.remove("ghost-99")


# ---------------------------------------------------------------------------
# heartbeat()
# ---------------------------------------------------------------------------

class TestHeartbeat:
    def test_heartbeat_updates_timestamp(self, registry, coder):
        agent_id = registry.register(coder)
        record_before = registry.list_agents()[0]
        ts_before = record_before.last_heartbeat

        ts_after = registry.heartbeat(agent_id)
        assert ts_after >= ts_before

    def test_heartbeat_revives_offline_agent(self, registry, coder):
        agent_id = registry.register(coder)
        registry.mark_offline(agent_id)
        assert registry.get_status(agent_id) == AgentStatus.OFFLINE

        registry.heartbeat(agent_id)
        assert registry.get_status(agent_id) == AgentStatus.IDLE

    def test_heartbeat_unknown_id_raises(self, registry):
        with pytest.raises(KeyError):
            registry.heartbeat("ghost-1")


# ---------------------------------------------------------------------------
# capabilities
# ---------------------------------------------------------------------------

class TestCapabilities:
    def test_get_capabilities_returns_agent_caps(self, registry, coder):
        agent_id = registry.register(coder)
        caps = registry.get_capabilities(agent_id)
        assert set(caps) == set(coder.capabilities)

    def test_capabilities_are_a_copy(self, registry, coder):
        agent_id = registry.register(coder)
        caps = registry.get_capabilities(agent_id)
        caps.append("should-not-mutate")
        assert "should-not-mutate" not in registry.get_capabilities(agent_id)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

class TestStatus:
    def test_initial_status_is_idle(self, registry, coder):
        agent_id = registry.register(coder)
        assert registry.get_status(agent_id) == AgentStatus.IDLE

    def test_set_status(self, registry, coder):
        agent_id = registry.register(coder)
        registry.set_status(agent_id, AgentStatus.BUSY)
        assert registry.get_status(agent_id) == AgentStatus.BUSY

    def test_mark_offline(self, registry, coder):
        agent_id = registry.register(coder)
        registry.mark_offline(agent_id)
        assert registry.get_status(agent_id) == AgentStatus.OFFLINE


# ---------------------------------------------------------------------------
# current task
# ---------------------------------------------------------------------------

class TestCurrentTask:
    def test_initial_current_task_is_none(self, registry, coder):
        agent_id = registry.register(coder)
        assert registry.get_current_task(agent_id) is None

    def test_set_current_task_marks_busy(self, registry, coder):
        from uuid import uuid4
        agent_id = registry.register(coder)
        task_id = uuid4()
        registry.set_current_task(agent_id, task_id)
        assert registry.get_status(agent_id) == AgentStatus.BUSY
        assert registry.get_current_task(agent_id) == task_id

    def test_clear_current_task_marks_idle(self, registry, coder):
        from uuid import uuid4
        agent_id = registry.register(coder)
        registry.set_current_task(agent_id, uuid4())
        registry.set_current_task(agent_id, None)
        assert registry.get_status(agent_id) == AgentStatus.IDLE
        assert registry.get_current_task(agent_id) is None


# ---------------------------------------------------------------------------
# get_agent_for_task()
# ---------------------------------------------------------------------------

class TestGetAgentForTask:
    def test_returns_capable_idle_agent(self, registry, coder):
        registry.register(coder)
        task = Task(description="Write some code", required_capabilities=["code"])
        record = registry.get_agent_for_task(task)
        assert record is not None
        assert isinstance(record.agent, CodingAgent)

    def test_returns_none_when_no_capable_agent(self, registry, researcher):
        registry.register(researcher)
        task = Task(description="Write some code", required_capabilities=["code"])
        record = registry.get_agent_for_task(task)
        assert record is None

    def test_returns_none_when_agent_is_busy(self, registry, coder):
        agent_id = registry.register(coder)
        registry.set_status(agent_id, AgentStatus.BUSY)
        task = Task(description="Write some code", required_capabilities=["code"])
        record = registry.get_agent_for_task(task)
        assert record is None

    def test_no_required_capabilities_matches_any_idle_agent(self, registry, coder):
        registry.register(coder)
        task = Task(description="Generic task")
        record = registry.get_agent_for_task(task)
        assert record is not None


# ---------------------------------------------------------------------------
# list_agents() / available_agents()
# ---------------------------------------------------------------------------

class TestListing:
    def test_list_agents_returns_all(self, registry, coder, researcher):
        registry.register(coder)
        registry.register(researcher)
        records = registry.list_agents()
        assert len(records) == 2

    def test_available_agents_excludes_busy(self, registry, coder, researcher):
        id1 = registry.register(coder)
        registry.register(researcher)
        registry.set_status(id1, AgentStatus.BUSY)
        available = registry.available_agents()
        assert len(available) == 1
        assert isinstance(available[0].agent, ResearchAgent)
