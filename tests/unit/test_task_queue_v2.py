"""Unit tests for queue/task_queue.py v2 — retry, cancel, dependency dispatch, overdue."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agents.coding import CodingAgent
from agents.research import ResearchAgent
from models.enums import Status
from models.task import Task
from task_queue.task_queue import TaskQueue


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_task(
    description: str = "task",
    priority: str = "medium",
    required_capabilities: list[str] | None = None,
    dependencies: list | None = None,
    deadline: datetime | None = None,
) -> Task:
    return Task(
        description=description,
        priority=priority,
        required_capabilities=required_capabilities or [],
        dependencies=dependencies or [],
        deadline=deadline,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def queue() -> TaskQueue:
    return TaskQueue()


@pytest.fixture()
def coder() -> CodingAgent:
    return CodingAgent()


@pytest.fixture()
def researcher() -> ResearchAgent:
    return ResearchAgent()


# ---------------------------------------------------------------------------
# Priority sorting (baseline sanity checks carried forward)
# ---------------------------------------------------------------------------

class TestPrioritySorting:
    def test_critical_before_low(self, queue):
        low = make_task("low", priority="low")
        critical = make_task("critical", priority="critical")
        queue.add_task(low)
        queue.add_task(critical)

        first = queue.get_next_task()
        assert first.description == "critical"

    def test_same_priority_fifo(self, queue):
        t1 = make_task("first", priority="medium")
        t2 = make_task("second", priority="medium")
        queue.add_task(t1)
        queue.add_task(t2)
        assert queue.get_next_task().description == "first"


# ---------------------------------------------------------------------------
# retry_task()
# ---------------------------------------------------------------------------

class TestRetryTask:
    def test_retry_moves_task_back_to_pending(self, queue):
        task = make_task()
        queue.add_task(task)
        queue.get_next_task()
        queue.fail_task(task.id, "boom")

        assert len(queue.failed_tasks()) == 1
        result = queue.retry_task(task.id)

        assert result is True
        assert len(queue.failed_tasks()) == 0
        assert len(queue.pending_tasks()) == 1

    def test_retry_increments_retry_count(self, queue):
        task = make_task()
        queue.add_task(task)
        queue.get_next_task()
        queue.fail_task(task.id)
        queue.retry_task(task.id)

        pending = queue.pending_tasks()
        assert pending[0].retry_count == 1

    def test_retry_multiple_times_increments_count(self, queue):
        task = make_task()
        queue.add_task(task)

        for expected_count in range(1, 4):
            queue.get_next_task()
            queue.fail_task(task.id)
            queue.retry_task(task.id)
            pending = queue.pending_tasks()
            assert pending[0].retry_count == expected_count

    def test_retry_unknown_task_returns_false(self, queue):
        from uuid import uuid4
        assert queue.retry_task(uuid4()) is False

    def test_retry_sets_status_to_pending(self, queue):
        task = make_task()
        queue.add_task(task)
        queue.get_next_task()
        queue.fail_task(task.id)
        queue.retry_task(task.id)

        pending = queue.pending_tasks()
        assert pending[0].status == Status.PENDING

    def test_retry_preserves_priority_sort(self, queue):
        low = make_task("low-retry", priority="low")
        high = make_task("high", priority="high")

        # Add low first, fail it.
        queue.add_task(low)
        queue.get_next_task()
        queue.fail_task(low.id)

        # Add high to pending.
        queue.add_task(high)

        # Retry low — it should still appear after high.
        queue.retry_task(low.id)
        first = queue.get_next_task()
        assert first.description == "high"


# ---------------------------------------------------------------------------
# cancel_task()
# ---------------------------------------------------------------------------

class TestCancelTask:
    def test_cancel_pending_task(self, queue):
        task = make_task()
        queue.add_task(task)

        result = queue.cancel_task(task.id)

        assert result is True
        assert len(queue.pending_tasks()) == 0
        assert len(queue.cancelled_tasks()) == 1

    def test_cancel_in_progress_task(self, queue):
        task = make_task()
        queue.add_task(task)
        queue.get_next_task()  # moves to in_progress

        result = queue.cancel_task(task.id)

        assert result is True
        assert len(queue.in_progress_tasks()) == 0
        assert len(queue.cancelled_tasks()) == 1

    def test_cancel_sets_status_cancelled(self, queue):
        task = make_task()
        queue.add_task(task)
        queue.cancel_task(task.id)

        cancelled = queue.cancelled_tasks()
        assert cancelled[0].status == Status.CANCELLED

    def test_cancel_unknown_task_returns_false(self, queue):
        from uuid import uuid4
        assert queue.cancel_task(uuid4()) is False

    def test_cancel_completed_task_returns_false(self, queue):
        task = make_task()
        queue.add_task(task)
        queue.get_next_task()
        queue.complete_task(task.id)

        assert queue.cancel_task(task.id) is False


# ---------------------------------------------------------------------------
# get_next_for_agent() — capability filtering
# ---------------------------------------------------------------------------

class TestGetNextForAgent:
    def test_dispatches_task_matching_agent_capabilities(self, queue, coder):
        task = make_task("code something", required_capabilities=["code"])
        queue.add_task(task)

        result = queue.get_next_for_agent(coder)
        assert result is not None
        assert result.description == "code something"

    def test_skips_task_not_matching_capabilities(self, queue, researcher):
        task = make_task("code something", required_capabilities=["code"])
        queue.add_task(task)

        result = queue.get_next_for_agent(researcher)
        assert result is None

    def test_dispatches_task_with_no_required_capabilities_to_any_agent(self, queue, researcher):
        task = make_task("generic task")
        queue.add_task(task)
        result = queue.get_next_for_agent(researcher)
        assert result is not None

    def test_skips_task_with_unmet_dependencies(self, queue, coder):
        from uuid import uuid4
        dep_id = uuid4()  # This ID will never be in completed set.
        task = make_task("blocked", required_capabilities=["code"], dependencies=[dep_id])
        queue.add_task(task)

        result = queue.get_next_for_agent(coder)
        assert result is None

    def test_dispatches_task_with_satisfied_dependencies(self, queue, coder):
        # Create and complete a dependency task manually.
        dep_task = make_task("dep", required_capabilities=["code"])
        queue.add_task(dep_task)
        dispatched_dep = queue.get_next_for_agent(coder)
        queue.complete_task(dispatched_dep.id)

        # Now the dependent task should be dispatchable.
        dependent = make_task("dependent", required_capabilities=["code"], dependencies=[dep_task.id])
        queue.add_task(dependent)
        result = queue.get_next_for_agent(coder)
        assert result is not None
        assert result.description == "dependent"

    def test_marks_task_in_progress_after_dispatch(self, queue, coder):
        task = make_task(required_capabilities=["code"])
        queue.add_task(task)
        result = queue.get_next_for_agent(coder)
        assert result.status == Status.IN_PROGRESS
        assert result.id in {t.id for t in queue.in_progress_tasks()}

    def test_priority_respected_during_capability_dispatch(self, queue, coder):
        low  = make_task("low",  priority="low",      required_capabilities=["code"])
        high = make_task("high", priority="high",     required_capabilities=["code"])
        queue.add_task(low)
        queue.add_task(high)

        result = queue.get_next_for_agent(coder)
        assert result.description == "high"


# ---------------------------------------------------------------------------
# overdue_tasks()
# ---------------------------------------------------------------------------

class TestOverdueTasks:
    def test_task_past_deadline_is_overdue(self, queue):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        task = make_task(deadline=past)
        queue.add_task(task)

        overdue = queue.overdue_tasks()
        assert len(overdue) == 1
        assert overdue[0].id == task.id

    def test_task_with_future_deadline_is_not_overdue(self, queue):
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        task = make_task(deadline=future)
        queue.add_task(task)

        assert queue.overdue_tasks() == []

    def test_task_without_deadline_is_never_overdue(self, queue):
        task = make_task()  # deadline=None
        queue.add_task(task)
        assert queue.overdue_tasks() == []

    def test_in_progress_task_past_deadline_is_overdue(self, queue):
        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        task = make_task(deadline=past)
        queue.add_task(task)
        queue.get_next_task()  # move to in_progress

        overdue = queue.overdue_tasks()
        assert len(overdue) == 1


# ---------------------------------------------------------------------------
# Task.retry_count & Task.deadline field defaults
# ---------------------------------------------------------------------------

class TestTaskModelFields:
    def test_retry_count_defaults_to_zero(self):
        task = Task(description="test")
        assert task.retry_count == 0

    def test_deadline_defaults_to_none(self):
        task = Task(description="test")
        assert task.deadline is None

    def test_retry_count_negative_raises(self):
        with pytest.raises(Exception):
            Task(description="bad", retry_count=-1)


# ---------------------------------------------------------------------------
# in_progress_tasks() / cancelled_tasks()
# ---------------------------------------------------------------------------

class TestNewReadMethods:
    def test_in_progress_tasks(self, queue):
        t1 = make_task("a")
        t2 = make_task("b")
        queue.add_task(t1)
        queue.add_task(t2)
        queue.get_next_task()
        assert len(queue.in_progress_tasks()) == 1

    def test_cancelled_tasks(self, queue):
        task = make_task()
        queue.add_task(task)
        queue.cancel_task(task.id)
        assert len(queue.cancelled_tasks()) == 1

    def test_repr_includes_cancelled(self, queue):
        task = make_task()
        queue.add_task(task)
        queue.cancel_task(task.id)
        assert "cancelled=1" in repr(queue)
