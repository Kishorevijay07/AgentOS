"""Unit tests for per-attempt execution ids on ResultStore (ADR-0010)."""
from __future__ import annotations

from uuid import uuid4

import pytest

from result_store import ResultStore
from result_store.models import ExecutionRecord


@pytest.fixture()
def store() -> ResultStore:
    return ResultStore()


class TestExecutionId:
    def test_record_has_unique_execution_id(self):
        r1 = ExecutionRecord(task_id=uuid4(), agent_id="A", started_at=_now())
        r2 = ExecutionRecord(task_id=uuid4(), agent_id="A", started_at=_now())
        assert r1.execution_id != r2.execution_id

    def test_retry_produces_distinct_records(self, store):
        task_id = uuid4()

        # Attempt 1 — fails.
        a1 = store.start_execution(task_id, agent_id="CoderA")
        store.finish_execution(task_id, output=None, success=False, error="boom")

        # Attempt 2 — succeeds.
        a2 = store.start_execution(task_id, agent_id="CoderA")
        store.finish_execution(task_id, output="ok", success=True)

        assert a1.execution_id != a2.execution_id
        executions = store.executions_for(task_id)
        assert len(executions) == 2
        assert [e.success for e in executions] == [False, True]

    def test_get_returns_latest_attempt(self, store):
        task_id = uuid4()
        store.start_execution(task_id, agent_id="A")
        store.finish_execution(task_id, output="first", success=False, error="x")
        store.start_execution(task_id, agent_id="A")
        latest = store.finish_execution(task_id, output="second", success=True)

        assert store.get(task_id) is latest
        assert store.get(task_id).output == "second"

    def test_get_by_execution_id(self, store):
        task_id = uuid4()
        rec = store.start_execution(task_id, agent_id="A")
        store.finish_execution(task_id, output="x", success=True)

        assert store.get_by_execution(rec.execution_id) is rec
        assert store.get_by_execution(uuid4()) is None

    def test_len_counts_executions_not_tasks(self, store):
        task_id = uuid4()
        store.start_execution(task_id, agent_id="A")
        store.finish_execution(task_id, output=None, success=False, error="x")
        store.start_execution(task_id, agent_id="A")
        store.finish_execution(task_id, output="ok", success=True)
        # Two attempts of one task → two records.
        assert len(store) == 2

    def test_cannot_open_two_executions_for_same_task(self, store):
        task_id = uuid4()
        store.start_execution(task_id, agent_id="A")
        with pytest.raises(ValueError):
            store.start_execution(task_id, agent_id="A")


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
