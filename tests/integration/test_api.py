"""
Integration tests for the HTTP API — driven with FastAPI's TestClient.

Forced into deterministic template mode (no LLM / no network) so the suite is
fast and repeatable.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import api.app as app_module


@pytest.fixture()
def client(monkeypatch):
    # Force template mode regardless of any .env key on the machine.
    monkeypatch.setattr(app_module, "_build_llm", lambda: None)
    with TestClient(app_module.app) as c:
        yield c


def _wait_done(client, run_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/runs/{run_id}").json()
        if s["status"] != "running":
            return s
        time.sleep(0.02)
    return client.get(f"/runs/{run_id}").json()


class TestMeta:
    def test_health(self, client):
        assert client.get("/health").json() == {"status": "ok"}

    def test_root_lists_endpoints(self, client):
        body = client.get("/").json()
        assert body["name"] == "AgentOS"
        assert any("POST /goals" in e for e in body["endpoints"])


class TestRunLifecycle:
    def test_submit_goal_returns_plan(self, client):
        r = client.post("/goals", json={"goal": "Build a REST API for a blog"})
        assert r.status_code == 202
        body = r.json()
        assert body["goal"] == "Build a REST API for a blog"
        assert len(body["plan"]) >= 1
        assert "run_id" in body

    def test_run_completes_and_reports(self, client):
        run_id = client.post("/goals", json={"goal": "x"}).json()["run_id"]
        summary = _wait_done(client, run_id)
        assert summary["status"] == "completed"
        assert summary["completed_tasks"] == summary["total_tasks"]
        assert summary["total_tasks"] >= 1

    def test_tasks_and_traces(self, client):
        run_id = client.post("/goals", json={"goal": "x"}).json()["run_id"]
        _wait_done(client, run_id)

        tasks = client.get(f"/runs/{run_id}/tasks").json()
        assert len(tasks) >= 1
        assert all(t["state"] == "completed" for t in tasks)
        assert all(t["origin"] == "planned" for t in tasks)

        traces = client.get(f"/runs/{run_id}/traces").json()
        assert len(traces) == len(tasks)
        assert all(tr["success"] for tr in traces)
        assert all(tr["output"] for tr in traces)

    def test_events_history(self, client):
        run_id = client.post("/goals", json={"goal": "x"}).json()["run_id"]
        _wait_done(client, run_id)
        events = client.get(f"/runs/{run_id}/events").json()
        types = {e["type"] for e in events}
        assert "task.created" in types
        assert "task.completed" in types
        # `since` slices the history.
        assert len(client.get(f"/runs/{run_id}/events?since=2").json()) == len(events) - 2

    def test_list_runs(self, client):
        client.post("/goals", json={"goal": "one"})
        client.post("/goals", json={"goal": "two"})
        goals = {r["goal"] for r in client.get("/runs").json()}
        assert {"one", "two"} <= goals

    def test_sse_stream_terminates(self, client):
        run_id = client.post("/goals", json={"goal": "x"}).json()["run_id"]
        _wait_done(client, run_id)
        # Stream should replay events and end with a `done` marker.
        with client.stream("GET", f"/runs/{run_id}/events/stream") as resp:
            assert resp.status_code == 200
            body = "".join(resp.iter_text())
        assert "task.completed" in body
        assert "event: done" in body


class TestErrors:
    def test_unknown_run_404(self, client):
        rid = "00000000-0000-0000-0000-000000000000"
        assert client.get(f"/runs/{rid}").status_code == 404
        assert client.get(f"/runs/{rid}/tasks").status_code == 404
        assert client.get(f"/runs/{rid}/traces").status_code == 404

    def test_empty_goal_rejected(self, client):
        assert client.post("/goals", json={"goal": ""}).status_code == 422
