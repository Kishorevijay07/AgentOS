"""
Integration test — full pipeline: Task → Scheduler → Worker → Events.

Tests the end-to-end flow:
  1. Task is created and pushed onto the TaskQueue.
  2. Scheduler dispatches it to an IDLE agent (returns AgentRecord).
  3. Worker executes the task.
  4. Events fire in the correct order on the Event Bus.
  5. Registry state is updated correctly throughout.
"""
from __future__ import annotations

from typing import Any, List

import pytest

from agents.base import BaseAgent
from agents.coding import CodingAgent
from agents.registry import AgentRegistry
from agents.research import ResearchAgent
from agents.worker import WorkerMixin, WorkerState
from events.bus import InMemoryEventBus
from events.event import Event
from events.event_type import EventType
from models.enums import AgentStatus
from models.task import Task
from scheduler.scheduler import Scheduler
from task_queue.task_queue import TaskQueue


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _TrackedAgent(WorkerMixin, BaseAgent):
    """Agent that records every task it executes."""

    capabilities: List[str] = ["code", "implement"]
    executed: List[Task]

    def __init__(self) -> None:
        self.executed = []

    def execute(self, task: Task) -> Any:
        self.executed.append(task)
        return f"done: {task.description}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture()
def registry() -> AgentRegistry:
    return AgentRegistry()


@pytest.fixture()
def task_queue() -> TaskQueue:
    return TaskQueue()


@pytest.fixture()
def agent() -> _TrackedAgent:
    return _TrackedAgent()


@pytest.fixture()
def scheduler(registry, bus) -> Scheduler:
    return Scheduler(registry=registry, bus=bus)


@pytest.fixture()
def pipeline(agent, registry, bus, scheduler, task_queue):
    """
    Fully wired pipeline:
      - agent initialised and wired to registry + bus
      - scheduler wired to registry + bus
      - task_queue ready
    """
    agent_id = registry.register(agent)
    agent._configure_worker(registry=registry, bus=bus, agent_id=agent_id)
    agent.initialize()  # INITIALIZING → IDLE + AGENT_ONLINE event
    return agent, agent_id, registry, bus, scheduler, task_queue


# ---------------------------------------------------------------------------
# Test: Task Created → Scheduled → Executed
# ---------------------------------------------------------------------------

class TestFullPipeline:
    def test_pipeline_executes_task_end_to_end(self, pipeline):
        agent, agent_id, registry, bus, scheduler, task_queue = pipeline

        # Collect all events
        events: List[Event] = []
        for et in EventType:
            bus.subscribe(et, lambda e: events.append(e))

        # 1. Create a task
        task = Task(description="Implement feature X", required_capabilities=["code"])
        task_queue.add_task(task)

        # 2. Dispatch via scheduler
        record = scheduler.dispatch(task)
        assert record is not None, "Scheduler returned None — no agent was dispatched"
        assert record.agent_id == agent_id

        # 3. Agent should now be BUSY in the registry
        assert registry.get_status(agent_id) == AgentStatus.BUSY
        assert registry.get_current_task(agent_id) == task.id

        # 4. Execute via the record's agent
        result = record.agent.execute(task)
        assert "done" in result
        assert task in agent.executed

        # 5. Mark task complete in registry
        registry.set_current_task(agent_id, None)  # clears task → IDLE
        assert registry.get_status(agent_id) == AgentStatus.IDLE

        # 6. Verify TASK_ASSIGNED event was published
        assigned_events = [e for e in events if e.type == EventType.TASK_ASSIGNED]
        assert len(assigned_events) == 1
        assert assigned_events[0].payload["task_id"] == str(task.id)
        assert assigned_events[0].payload["agent_id"] == agent_id

    def test_agent_online_event_fires_on_initialize(self, pipeline):
        _, agent_id, _, bus, *_ = pipeline
        # AGENT_ONLINE was fired during pipeline setup (agent.initialize())
        online_events = [e for e in bus.history() if e.type == EventType.AGENT_ONLINE]
        assert len(online_events) >= 1
        assert online_events[0].payload["agent_id"] == agent_id

    def test_event_order_is_online_then_assigned(self, pipeline):
        agent, agent_id, registry, bus, scheduler, _ = pipeline

        task = Task(description="Test ordering", required_capabilities=["code"])
        scheduler.dispatch(task)

        history = bus.history()
        types = [e.type for e in history]

        online_idx = next(i for i, t in enumerate(types) if t == EventType.AGENT_ONLINE)
        assigned_idx = next(i for i, t in enumerate(types) if t == EventType.TASK_ASSIGNED)
        assert online_idx < assigned_idx, "AGENT_ONLINE must precede TASK_ASSIGNED"

    def test_no_dispatch_when_agent_busy(self, pipeline):
        agent, agent_id, registry, bus, scheduler, _ = pipeline

        t1 = Task(description="First task", required_capabilities=["code"])
        t2 = Task(description="Second task", required_capabilities=["code"])

        scheduler.dispatch(t1)
        # Agent is now BUSY — second dispatch should fail
        record = scheduler.dispatch(t2)
        assert record is None

    def test_heartbeat_during_pipeline(self, pipeline):
        agent, agent_id, registry, bus, scheduler, _ = pipeline

        received: List[Event] = []
        bus.subscribe(EventType.AGENT_HEARTBEAT, lambda e: received.append(e))

        ts = agent.heartbeat()
        assert ts is not None
        assert len(received) == 1
        assert received[0].payload["agent_id"] == agent_id

        # Registry heartbeat timestamp updated
        record = next(r for r in registry.list_agents() if r.agent_id == agent_id)
        assert record.last_heartbeat >= ts


# ---------------------------------------------------------------------------
# Test: Multiple agents in pipeline
# ---------------------------------------------------------------------------

class TestMultiAgentPipeline:
    def test_scheduler_picks_correct_agent_for_each_task(self, bus, registry, task_queue):
        coder = CodingAgent()
        researcher = ResearchAgent()

        c_id = registry.register(coder)
        r_id = registry.register(researcher)

        coder._configure_worker(registry=registry, bus=bus, agent_id=c_id)
        researcher._configure_worker(registry=registry, bus=bus, agent_id=r_id)
        coder.initialize()
        researcher.initialize()

        scheduler = Scheduler(registry=registry, bus=bus)

        code_task = Task(description="Write code", required_capabilities=["code"])
        research_task = Task(description="Research topic", required_capabilities=["research"])

        c_record = scheduler.dispatch(code_task)
        r_record = scheduler.dispatch(research_task)

        assert c_record is not None and isinstance(c_record.agent, CodingAgent)
        assert r_record is not None and isinstance(r_record.agent, ResearchAgent)

    def test_two_tasks_dispatched_to_different_idle_agents(self, bus, registry, task_queue):
        a1 = _TrackedAgent()
        a2 = _TrackedAgent()

        id1 = registry.register(a1)
        id2 = registry.register(a2)

        scheduler = Scheduler(registry=registry, bus=bus)

        t1 = Task(description="Task 1", required_capabilities=["code"])
        t2 = Task(description="Task 2", required_capabilities=["code"])

        r1 = scheduler.dispatch(t1)
        r2 = scheduler.dispatch(t2)

        # Both dispatched to different agents
        assert r1 is not None
        assert r2 is not None
        assert r1.agent_id != r2.agent_id


# ---------------------------------------------------------------------------
# Test: Shutdown lifecycle in pipeline
# ---------------------------------------------------------------------------

class TestShutdownInPipeline:
    def test_agent_offline_event_on_shutdown(self, pipeline):
        agent, agent_id, _, bus, *_ = pipeline

        received: List[Event] = []
        bus.subscribe(EventType.AGENT_OFFLINE, lambda e: received.append(e))

        agent.shutdown()

        assert len(received) == 1
        assert received[0].payload["agent_id"] == agent_id
        assert agent.worker_state == WorkerState.TERMINATED

    def test_terminated_agent_not_dispatched_to(self, pipeline):
        agent, agent_id, registry, bus, scheduler, _ = pipeline

        # Shut the agent down — registry still has it but it's TERMINATED
        agent.shutdown()
        # Manually mark offline so scheduler skips it
        registry.mark_offline(agent_id)

        task = Task(description="After shutdown", required_capabilities=["code"])
        record = scheduler.dispatch(task)
        assert record is None
