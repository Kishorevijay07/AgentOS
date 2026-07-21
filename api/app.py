from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import List
from uuid import UUID

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from api.models import CreatedRun, GoalRequest, RunSummary, TaskView, TraceView
from api.service import RunManager

logger = logging.getLogger("agentos.api")


def _build_llm():
    """Return an OpenRouter client if a key is configured, else None (template mode)."""
    try:
        from services.openrouter import OpenRouterLLMClient

        return OpenRouterLLMClient.from_env()
    except Exception:  # noqa: BLE001 — no key / no dep → deterministic template mode
        logger.info("No LLM configured; API runs in deterministic template mode.")
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.manager = RunManager(llm=_build_llm())
    logger.info("AgentOS API started.")
    try:
        yield
    finally:
        app.state.manager.shutdown()
        logger.info("AgentOS API stopped.")


app = FastAPI(
    title="AgentOS",
    version="1.0.0",
    summary="An operating system for AI agents — HTTP control plane.",
    lifespan=lifespan,
)


def _manager(app: FastAPI) -> RunManager:
    return app.state.manager


# --------------------------------------------------------------------------- #
#  Meta
# --------------------------------------------------------------------------- #

@app.get("/", tags=["meta"])
def root() -> dict:
    return {
        "name": "AgentOS",
        "version": "1.0.0",
        "docs": "/docs",
        "endpoints": ["POST /goals", "GET /runs", "GET /runs/{id}",
                      "GET /runs/{id}/tasks", "GET /runs/{id}/traces",
                      "GET /runs/{id}/events", "GET /runs/{id}/events/stream"],
    }


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
#  Runs
# --------------------------------------------------------------------------- #

@app.post("/goals", response_model=CreatedRun, status_code=202, tags=["runs"])
def submit_goal(body: GoalRequest) -> CreatedRun:
    """Plan a goal and start executing it. Returns the run id and initial plan."""
    return _manager(app).create_run(
        body.goal, max_steps=body.max_steps, max_replans=body.max_replans
    )


@app.get("/runs", response_model=List[RunSummary], tags=["runs"])
def list_runs() -> List[RunSummary]:
    return _manager(app).list_runs()


@app.get("/runs/{run_id}", response_model=RunSummary, tags=["runs"])
def get_run(run_id: UUID) -> RunSummary:
    summary = _manager(app).summary(run_id)
    if summary is None:
        raise HTTPException(404, f"No run {run_id}.")
    return summary


@app.get("/runs/{run_id}/tasks", response_model=List[TaskView], tags=["runs"])
def get_tasks(run_id: UUID) -> List[TaskView]:
    tasks = _manager(app).tasks(run_id)
    if tasks is None:
        raise HTTPException(404, f"No run {run_id}.")
    return tasks


@app.get("/runs/{run_id}/traces", response_model=List[TraceView], tags=["runs"])
def get_traces(run_id: UUID) -> List[TraceView]:
    traces = _manager(app).traces(run_id)
    if traces is None:
        raise HTTPException(404, f"No run {run_id}.")
    return traces


@app.get("/runs/{run_id}/events", tags=["runs"])
def get_events(run_id: UUID, since: int = 0) -> List[dict]:
    """Poll event history from index *since* (newest last)."""
    events = _manager(app).events(run_id, since=since)
    if events is None:
        raise HTTPException(404, f"No run {run_id}.")
    return events


@app.get("/runs/{run_id}/events/stream", tags=["runs"])
async def stream_events(run_id: UUID) -> StreamingResponse:
    """Server-Sent Events stream of a run's events until it finishes."""
    manager = _manager(app)
    if manager.summary(run_id) is None:
        raise HTTPException(404, f"No run {run_id}.")

    async def event_source():
        seen = 0
        while True:
            batch = manager.events(run_id, since=seen) or []
            for event in batch:
                seen = event["index"] + 1
                yield f"data: {json.dumps(event)}\n\n"
            if not manager.is_active(run_id):
                # Flush any final events, then close.
                final = manager.events(run_id, since=seen) or []
                for event in final:
                    seen = event["index"] + 1
                    yield f"data: {json.dumps(event)}\n\n"
                yield "event: done\ndata: {}\n\n"
                return
            await asyncio.sleep(0.25)

    return StreamingResponse(event_source(), media_type="text/event-stream")
