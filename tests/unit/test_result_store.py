"""Unit tests for result_store/ (Module 6 — ResultStore)."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import List
from uuid import uuid4

import pytest

from result_store import Artifact, ExecutionRecord, LogEntry, LogLevel, ResultStore
from result_store.models import ExecutionRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tid():
    return uuid4()


def _open_record(store: ResultStore, agent_id: str = "TestAgent-1") -> tuple:
    task_id = _tid()
    record = store.start_execution(task_id, agent_id=agent_id)
    return task_id, record


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def store() -> ResultStore:
    return ResultStore()


@pytest.fixture()
def task_id():
    return _tid()


# ---------------------------------------------------------------------------
# start_execution
# ---------------------------------------------------------------------------

class TestStartExecution:
    def test_returns_execution_record(self, store, task_id):
        record = store.start_execution(task_id, agent_id="CodingAgent-1")
        assert isinstance(record, ExecutionRecord)

    def test_record_is_open(self, store, task_id):
        record = store.start_execution(task_id, agent_id="CodingAgent-1")
        assert record.is_open is True
        assert record.ended_at is None
        assert record.success is None

    def test_record_has_correct_task_id(self, store, task_id):
        record = store.start_execution(task_id, agent_id="CodingAgent-1")
        assert record.task_id == task_id

    def test_record_has_correct_agent_id(self, store, task_id):
        record = store.start_execution(task_id, agent_id="ResearchAgent-2")
        assert record.agent_id == "ResearchAgent-2"

    def test_started_at_is_utc(self, store, task_id):
        record = store.start_execution(task_id, agent_id="A")
        assert record.started_at.tzinfo == timezone.utc

    def test_double_start_same_task_raises(self, store, task_id):
        store.start_execution(task_id, agent_id="A")
        with pytest.raises(ValueError, match="already exists"):
            store.start_execution(task_id, agent_id="A")

    def test_different_task_ids_are_independent(self, store):
        t1, t2 = _tid(), _tid()
        r1 = store.start_execution(t1, agent_id="A")
        r2 = store.start_execution(t2, agent_id="B")
        assert r1.task_id != r2.task_id
        assert len(store) == 2


# ---------------------------------------------------------------------------
# finish_execution
# ---------------------------------------------------------------------------

class TestFinishExecution:
    def test_sets_success_true(self, store, task_id):
        store.start_execution(task_id, agent_id="A")
        record = store.finish_execution(task_id, output="result", success=True)
        assert record.success is True

    def test_sets_output(self, store, task_id):
        store.start_execution(task_id, agent_id="A")
        record = store.finish_execution(task_id, output={"data": 42}, success=True)
        assert record.output == {"data": 42}

    def test_sets_error_on_failure(self, store, task_id):
        store.start_execution(task_id, agent_id="A")
        record = store.finish_execution(task_id, output=None, success=False, error="boom")
        assert record.success is False
        assert record.error == "boom"

    def test_sets_ended_at(self, store, task_id):
        store.start_execution(task_id, agent_id="A")
        record = store.finish_execution(task_id, output=None, success=True)
        assert record.ended_at is not None
        assert record.ended_at.tzinfo == timezone.utc

    def test_record_is_closed_after_finish(self, store, task_id):
        store.start_execution(task_id, agent_id="A")
        record = store.finish_execution(task_id, output=None, success=True)
        assert record.is_open is False

    def test_finish_without_start_raises_key_error(self, store, task_id):
        with pytest.raises(KeyError):
            store.finish_execution(task_id, output=None, success=True)

    def test_finish_already_closed_raises_runtime_error(self, store, task_id):
        store.start_execution(task_id, agent_id="A")
        store.finish_execution(task_id, output=None, success=True)
        with pytest.raises(RuntimeError, match="already closed"):
            store.finish_execution(task_id, output=None, success=True)

    def test_duration_seconds_is_non_negative(self, store, task_id):
        store.start_execution(task_id, agent_id="A")
        time.sleep(0.01)
        record = store.finish_execution(task_id, output=None, success=True)
        assert record.duration_seconds is not None
        assert record.duration_seconds >= 0.0


# ---------------------------------------------------------------------------
# duration_seconds
# ---------------------------------------------------------------------------

class TestDurationSeconds:
    def test_none_while_open(self, store, task_id):
        record = store.start_execution(task_id, agent_id="A")
        assert record.duration_seconds is None

    def test_positive_after_close(self, store, task_id):
        store.start_execution(task_id, agent_id="A")
        time.sleep(0.01)
        record = store.finish_execution(task_id, output=None, success=True)
        assert record.duration_seconds > 0

    def test_computed_correctly(self):
        """Verify duration via manually set timestamps."""
        record = ExecutionRecord(
            task_id=uuid4(),
            agent_id="A",
            started_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            ended_at=datetime(2026, 1, 1, 12, 0, 3, tzinfo=timezone.utc),
        )
        assert record.duration_seconds == 3.0


# ---------------------------------------------------------------------------
# add_log
# ---------------------------------------------------------------------------

class TestAddLog:
    def test_log_appended_to_record(self, store, task_id):
        store.start_execution(task_id, agent_id="A")
        store.add_log(task_id, LogLevel.INFO, "Hello", source="Agent")
        record = store.get(task_id)
        assert len(record.logs) == 1
        assert record.logs[0].message == "Hello"
        assert record.logs[0].level == LogLevel.INFO
        assert record.logs[0].source == "Agent"

    def test_multiple_logs_in_order(self, store, task_id):
        store.start_execution(task_id, agent_id="A")
        store.add_log(task_id, LogLevel.DEBUG, "First")
        store.add_log(task_id, LogLevel.INFO, "Second")
        store.add_log(task_id, LogLevel.ERROR, "Third")
        record = store.get(task_id)
        assert [e.message for e in record.logs] == ["First", "Second", "Third"]

    def test_log_entry_has_utc_timestamp(self, store, task_id):
        store.start_execution(task_id, agent_id="A")
        entry = store.add_log(task_id, LogLevel.WARNING, "warn")
        assert entry.timestamp.tzinfo == timezone.utc

    def test_add_log_returns_log_entry(self, store, task_id):
        store.start_execution(task_id, agent_id="A")
        entry = store.add_log(task_id, LogLevel.INFO, "msg")
        assert isinstance(entry, LogEntry)

    def test_add_log_unknown_task_raises(self, store, task_id):
        with pytest.raises(KeyError):
            store.add_log(task_id, LogLevel.INFO, "msg")

    def test_log_after_finish_is_allowed(self, store, task_id):
        """Logs may be added after closing (e.g. teardown notes)."""
        store.start_execution(task_id, agent_id="A")
        store.finish_execution(task_id, output=None, success=True)
        store.add_log(task_id, LogLevel.DEBUG, "teardown note")
        assert len(store.get(task_id).logs) == 1

    def test_all_log_levels_accepted(self, store, task_id):
        store.start_execution(task_id, agent_id="A")
        for level in LogLevel:
            store.add_log(task_id, level, f"msg at {level}")
        assert len(store.get(task_id).logs) == len(list(LogLevel))


# ---------------------------------------------------------------------------
# add_artifact
# ---------------------------------------------------------------------------

class TestAddArtifact:
    def test_artifact_appended(self, store, task_id):
        store.start_execution(task_id, agent_id="A")
        store.add_artifact(task_id, "report.md", "# Report")
        record = store.get(task_id)
        assert len(record.artifacts) == 1
        assert record.artifacts[0].name == "report.md"
        assert record.artifacts[0].content == "# Report"

    def test_artifact_media_type_default(self, store, task_id):
        store.start_execution(task_id, agent_id="A")
        artifact = store.add_artifact(task_id, "out.txt", "data")
        assert artifact.media_type == "text/plain"

    def test_artifact_custom_media_type(self, store, task_id):
        store.start_execution(task_id, agent_id="A")
        artifact = store.add_artifact(
            task_id, "out.json", '{"k":1}', media_type="application/json"
        )
        assert artifact.media_type == "application/json"

    def test_multiple_artifacts_preserved(self, store, task_id):
        store.start_execution(task_id, agent_id="A")
        store.add_artifact(task_id, "a.txt", "aaa")
        store.add_artifact(task_id, "b.json", {"x": 1}, media_type="application/json")
        record = store.get(task_id)
        assert len(record.artifacts) == 2
        assert record.artifacts[1].name == "b.json"

    def test_add_artifact_bytes_content(self, store, task_id):
        store.start_execution(task_id, agent_id="A")
        artifact = store.add_artifact(task_id, "img.png", b"\x89PNG", media_type="image/png")
        assert isinstance(artifact.content, bytes)

    def test_add_artifact_unknown_task_raises(self, store, task_id):
        with pytest.raises(KeyError):
            store.add_artifact(task_id, "f.txt", "data")

    def test_add_artifact_returns_artifact(self, store, task_id):
        store.start_execution(task_id, agent_id="A")
        a = store.add_artifact(task_id, "x.txt", "y")
        assert isinstance(a, Artifact)


# ---------------------------------------------------------------------------
# get / all / successful / failed / open_executions / query
# ---------------------------------------------------------------------------

class TestQuerying:
    def _setup_three(self, store):
        """Create three records: one open, one successful, one failed."""
        t_open = _tid()
        t_ok   = _tid()
        t_fail = _tid()

        store.start_execution(t_open, agent_id="A")

        store.start_execution(t_ok, agent_id="B")
        store.finish_execution(t_ok, output="done", success=True)

        store.start_execution(t_fail, agent_id="C")
        store.finish_execution(t_fail, output=None, success=False, error="err")

        return t_open, t_ok, t_fail

    def test_get_returns_correct_record(self, store):
        t, _ = _open_record(store)
        record = store.get(t)
        assert record is not None
        assert record.task_id == t

    def test_get_unknown_task_returns_none(self, store):
        assert store.get(_tid()) is None

    def test_all_returns_all_records(self, store):
        self._setup_three(store)
        assert len(store.all()) == 3

    def test_successful_filters_correctly(self, store):
        self._setup_three(store)
        ok = store.successful()
        assert len(ok) == 1
        assert ok[0].success is True

    def test_failed_filters_correctly(self, store):
        self._setup_three(store)
        fail = store.failed()
        assert len(fail) == 1
        assert fail[0].success is False

    def test_open_executions_filters_correctly(self, store):
        self._setup_three(store)
        open_ = store.open_executions()
        assert len(open_) == 1
        assert open_[0].is_open

    def test_query_by_agent_id(self, store):
        self._setup_three(store)
        results = store.query(agent_id="B")
        assert len(results) == 1
        assert results[0].agent_id == "B"

    def test_query_by_success(self, store):
        self._setup_three(store)
        assert len(store.query(success=True)) == 1
        assert len(store.query(success=False)) == 1

    def test_query_by_since(self, store):
        self._setup_three(store)
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        assert store.query(since=future) == []

        past = datetime.now(timezone.utc) - timedelta(hours=1)
        assert len(store.query(since=past)) == 3

    def test_query_combined_filters(self, store):
        self._setup_three(store)
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        results = store.query(agent_id="B", since=past, success=True)
        assert len(results) == 1

    def test_query_returns_sorted_by_started_at(self, store):
        for i in range(5):
            t = _tid()
            store.start_execution(t, agent_id=f"A-{i}")
        results = store.query()
        timestamps = [r.started_at for r in results]
        assert timestamps == sorted(timestamps)


# ---------------------------------------------------------------------------
# len / repr
# ---------------------------------------------------------------------------

class TestMeta:
    def test_len_grows_with_records(self, store):
        assert len(store) == 0
        _open_record(store)
        assert len(store) == 1
        _open_record(store)
        assert len(store) == 2

    def test_repr_contains_counts(self, store):
        t, _ = _open_record(store)
        store.finish_execution(t, output=None, success=True)
        r = repr(store)
        assert "total=1" in r
        assert "successful=1" in r


# ---------------------------------------------------------------------------
# LogLevel enum
# ---------------------------------------------------------------------------

class TestLogLevel:
    def test_four_levels_exist(self):
        names = {lv.name for lv in LogLevel}
        assert names == {"DEBUG", "INFO", "WARNING", "ERROR"}

    def test_values_are_strings(self):
        for lv in LogLevel:
            assert isinstance(lv.value, str)


# ---------------------------------------------------------------------------
# Thread-safety smoke test
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_start_and_log(self, store):
        """Multiple threads starting executions must not corrupt the store."""
        task_ids = [_tid() for _ in range(20)]
        errors: List[Exception] = []

        def worker(task_id):
            try:
                store.start_execution(task_id, agent_id="Thread-Agent")
                store.add_log(task_id, LogLevel.INFO, "concurrent")
                store.finish_execution(task_id, output="ok", success=True)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(tid,)) for tid in task_ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        assert len(store) == 20
        assert len(store.successful()) == 20
