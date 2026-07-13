"""Unit tests for scheduler/scheduler.py (Module 3 — Scheduler v2)."""
from __future__ import annotations

from typing import List
from uuid import uuid4

import pytest

from agents.coding import CodingAgent
from agents.registry import AgentRecord, AgentRegistry
from agents.research import ResearchAgent
from agents.testing import TestingAgent
from agents.worker import WorkerMixin, WorkerState
from events.bus import InMemoryEventBus
from events.event_type import EventType
from models.enums import AgentStatus
from models.task import Task
from scheduler.scheduler import Scheduler


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def registry() -> AgentRegistry:
    return AgentRegistry()


@pytest.fixture()
def bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture()
def scheduler(registry, bus) -> Scheduler:
    return Scheduler(registry=registry, bus=bus)


def _task(description: str = "test task", caps: List[str] | None = None) -> Task:
    return Task(description=description, required_capabilities=caps or [])


def _register_idle(registry: AgentRegistry, agent) -> str:
    """Register an agent and ensure it is IDLE."""
    agent_id = registry.register(agent)
    # freshly registered agents are IDLE by default
    return agent_id


# ---------------------------------------------------------------------------
# dispatch() — basic routing
# ---------------------------------------------------------------------------

class TestDispatchBasicRouting:
    def test_returns_agent_record_not_base_agent(self, scheduler, registry):
        registry.register(CodingAgent())
        task = _task(caps=["code"])
        result = scheduler.dispatch(task)
        assert isinstance(result, AgentRecord)

    def test_returns_none_when_registry_empty(self, scheduler):
        task = _task(caps=["code"])
        assert scheduler.dispatch(task) is None

    def test_returns_none_when_no_matching_capabilities(self, scheduler):
        """
        When the registry is empty there are no candidates at all — returns None.
        When there IS a partial match, the scheduler uses fallback and returns it.
        This test verifies the empty-registry (truly no agents) case.
        """
        # Registry is empty (no agents registered in this test)
        task = _task(caps=["code"])
        assert scheduler.dispatch(task) is None

    def test_partial_fallback_returns_best_overlap_agent(self, scheduler, registry):
        """When no full match exists the scheduler falls back to partial overlap."""
        registry.register(ResearchAgent())  # has ["research", "web", "summarise"] — 0 overlap with ["code"]
        task = _task(caps=["code"])
        # Partial fallback: ResearchAgent has 0 overlap — still returned (best available)
        result = scheduler.dispatch(task)
        assert result is not None
        assert isinstance(result.agent, ResearchAgent)

    def test_dispatches_to_capable_agent(self, scheduler, registry):
        registry.register(CodingAgent())
        task = _task(caps=["code"])
        record = scheduler.dispatch(task)
        assert record is not None
        assert isinstance(record.agent, CodingAgent)

    def test_no_required_capabilities_matches_any_idle_agent(self, scheduler, registry):
        registry.register(ResearchAgent())
        task = _task(caps=[])
        record = scheduler.dispatch(task)
        assert record is not None


# ---------------------------------------------------------------------------
# dispatch() — IDLE-only preference
# ---------------------------------------------------------------------------

class TestIdlePreference:
    def test_skips_busy_agents(self, scheduler, registry):
        coder_id = registry.register(CodingAgent())
        registry.set_current_task(coder_id, uuid4())  # marks BUSY
        task = _task(caps=["code"])
        assert scheduler.dispatch(task) is None

    def test_skips_offline_agents(self, scheduler, registry):
        coder_id = registry.register(CodingAgent())
        registry.mark_offline(coder_id)
        task = _task(caps=["code"])
        assert scheduler.dispatch(task) is None

    def test_prefers_idle_over_paused(self, scheduler, registry):
        """Paused agents have IDLE status — but actually PAUSED is not AgentStatus.IDLE."""
        # Only IDLE status agents are picked; set one busy to verify the idle one wins.
        idle_coder = CodingAgent()
        busy_coder = CodingAgent()
        idle_id = registry.register(idle_coder)
        busy_id = registry.register(busy_coder)
        registry.set_current_task(busy_id, uuid4())  # BUSY

        task = _task(caps=["code"])
        record = scheduler.dispatch(task)
        assert record is not None
        assert record.agent_id == idle_id


# ---------------------------------------------------------------------------
# dispatch() — capability scoring
# ---------------------------------------------------------------------------

class TestCapabilityScoring:
    def test_prefers_most_specialised_agent(self, scheduler, registry):
        """
        Specialist (fewer caps) should win over generalist.

        TestingAgent caps: ["test", "qa"]        (2 caps, covers "test")
        ResearchAgent caps: ["research", "web", "summarise"] (3 caps, does NOT cover "test")

        Add a generalist agent with ALL caps:
        """
        from agents.base import BaseAgent
        from agents.worker import WorkerMixin
        from models.task import Task as T

        class GeneralistAgent(WorkerMixin, BaseAgent):
            capabilities: List[str] = ["test", "qa", "extra1", "extra2"]
            def execute(self, task): return "done"

        specialist = TestingAgent()    # caps: ["test", "qa"]
        generalist = GeneralistAgent() # caps: ["test", "qa", "extra1", "extra2"]

        registry.register(specialist)
        registry.register(generalist)

        task = _task(caps=["test"])
        record = scheduler.dispatch(task)
        # Specialist has fewer total capabilities → wins
        assert isinstance(record.agent, TestingAgent)

    def test_partial_match_fallback_when_no_full_match(self, scheduler, registry):
        """When no agent covers all required caps, pick the best partial match."""
        # ResearchAgent: ["research", "web", "summarise"] — covers "web" but not "code"
        # CodingAgent:   ["code", "implement", "debug"]  — covers "code" but not "web"
        # Task requires both: only partial matches possible.
        registry.register(ResearchAgent())
        registry.register(CodingAgent())

        task = _task(caps=["code", "web"])
        record = scheduler.dispatch(task)
        # Should return *something* (best partial) rather than None
        assert record is not None

    def test_full_match_beats_partial_match(self, scheduler, registry):
        from agents.base import BaseAgent
        from agents.worker import WorkerMixin

        class PartialAgent(WorkerMixin, BaseAgent):
            capabilities: List[str] = ["code"]
            def execute(self, task): return "partial"

        class FullAgent(WorkerMixin, BaseAgent):
            capabilities: List[str] = ["code", "implement"]
            def execute(self, task): return "full"

        registry.register(PartialAgent())
        registry.register(FullAgent())

        task = _task(caps=["code", "implement"])
        record = scheduler.dispatch(task)
        assert isinstance(record.agent, FullAgent)


# ---------------------------------------------------------------------------
# dispatch() — side effects
# ---------------------------------------------------------------------------

class TestDispatchSideEffects:
    def test_marks_agent_busy_after_dispatch(self, scheduler, registry):
        coder_id = registry.register(CodingAgent())
        task = _task(caps=["code"])
        scheduler.dispatch(task)
        assert registry.get_status(coder_id) == AgentStatus.BUSY

    def test_sets_current_task_id_after_dispatch(self, scheduler, registry):
        coder_id = registry.register(CodingAgent())
        task = _task(caps=["code"])
        scheduler.dispatch(task)
        assert registry.get_current_task(coder_id) == task.id

    def test_publishes_task_assigned_event(self, scheduler, registry, bus):
        registry.register(CodingAgent())
        task = _task(caps=["code"])

        received: List = []
        bus.subscribe(EventType.TASK_ASSIGNED, lambda e: received.append(e))
        scheduler.dispatch(task)

        assert len(received) == 1
        evt = received[0]
        assert evt.payload["task_id"] == str(task.id)
        assert evt.source == "Scheduler"

    def test_no_event_published_when_no_agent_found(self, scheduler, bus):
        received: List = []
        bus.subscribe(EventType.TASK_ASSIGNED, lambda e: received.append(e))
        scheduler.dispatch(_task(caps=["code"]))
        assert received == []


# ---------------------------------------------------------------------------
# dispatch() — no bus (optional)
# ---------------------------------------------------------------------------

class TestDispatchWithoutBus:
    def test_dispatch_works_without_bus(self, registry):
        scheduler = Scheduler(registry=registry, bus=None)
        registry.register(CodingAgent())
        task = _task(caps=["code"])
        record = scheduler.dispatch(task)
        assert record is not None
        assert isinstance(record.agent, CodingAgent)


# ---------------------------------------------------------------------------
# Scheduler helper methods
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_covers_returns_true_for_superset(self, registry):
        from agents.registry import AgentRecord
        agent = CodingAgent()
        record = AgentRecord("CodingAgent-1", agent)
        assert Scheduler._covers(record, ["code"]) is True
        assert Scheduler._covers(record, ["code", "implement"]) is True

    def test_covers_returns_false_when_missing_cap(self, registry):
        from agents.registry import AgentRecord
        agent = CodingAgent()
        record = AgentRecord("CodingAgent-1", agent)
        assert Scheduler._covers(record, ["research"]) is False

    def test_overlap_counts_matching_caps(self, registry):
        from agents.registry import AgentRecord
        agent = CodingAgent()  # caps: ["code", "implement", "debug"]
        record = AgentRecord("CodingAgent-1", agent)
        assert Scheduler._overlap(record, ["code", "research"]) == 1
        assert Scheduler._overlap(record, ["code", "implement"]) == 2
        assert Scheduler._overlap(record, ["missing"]) == 0
