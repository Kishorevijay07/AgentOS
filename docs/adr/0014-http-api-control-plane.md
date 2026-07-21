# ADR-0014: HTTP API control plane

- **Status:** Accepted
- **Date:** 2026-07-22

## Context

Through v0.9 AgentOS was a *library*: you imported the Kernel and drove it from
Python. To be a usable platform it needs a network control plane — submit a goal
over HTTP, watch it run, read its results — so non-Python clients, UIs, and other
services can use it. And for cross-machine crash-resume, the checkpoint store
needs a shared backend.

## Decision

We will add a FastAPI service (`api/`) over the Kernel and a
`RedisCheckpointStore`.

- **`RunManager`** (`api/service.py`): the application service. Each submitted
  goal becomes a self-contained `Run` — its own Kernel (graph + worker pool +
  bus + result store) executing in a daemon thread via `run_until_idle`. The
  manager holds only lifecycle and lookup; all domain logic stays in the Kernel.
  Intelligence is automatic: an `OPENROUTER_API_KEY` in the environment turns on
  LLM planning/reflection/workers; otherwise the deterministic `TemplatePlanner`
  and placeholder workers keep the service runnable with no key.
- **Routes** (`api/app.py`): `POST /goals` (plan + start, returns the plan),
  `GET /runs`, `GET /runs/{id}` (status + health), `/tasks`, `/traces`,
  `/events` (poll) and `/events/stream` (Server-Sent Events until the run ends),
  plus `/health` and the auto-generated OpenAPI UI at `/docs`. DTOs are Pydantic
  (`api/models.py`), so request/response validation and the schema are free.
- **`RedisCheckpointStore`** (`checkpoint/redis_store.py`): the same
  `CheckpointStore` port ([ADR-0013](0013-checkpointing-and-resume.md)) over
  Redis `GET`/`SET`, so a run checkpointed on one node resumes on another — the
  cross-machine half of crash-resume, and a one-line construction swap.

The API is a thin **adapter** in the outermost ring of the clean architecture:
it depends inward on the Kernel and never the reverse, so everything below it is
unchanged and still usable as a library.

## Consequences

- AgentOS is now a running service: `uvicorn api.app:app`, then
  `POST /goals` and poll/stream. Multiple goals run concurrently, each isolated
  in its own Kernel.
- Fixed a latent runtime race the concurrent API exposed: `execute_task` now only
  releases a worker that is still `BUSY`, so a `shutdown()` that moves a busy
  worker `OFFLINE` mid-execution no longer triggers an illegal `OFFLINE → IDLE`
  transition.
- Known limits (v1.0 scope): runs live in memory (a process restart loses the
  run registry, though individual runs can checkpoint); no auth/rate-limiting;
  one worker pool per run rather than a shared elastic pool. All are natural
  follow-ons and none require touching the core runtime.
