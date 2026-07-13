# ADR-0004: Thread-safe Agent Registry

- **Status:** Accepted
- **Date:** 2026-07-11

## Context

The Scheduler needs to answer "which workers exist, what can they do, and which
are idle?" without holding references to concrete agent objects or knowing how
they were built. Multiple agents (and, later, multiple threads/processes) mutate
this directory concurrently — registration, heartbeats, status changes — so it
must be thread-safe. It must also carry enough per-worker metadata to support
remote workers eventually.

## Decision

We will keep a central `AgentRegistry` (behind `AbstractAgentRegistry`) that
stores an **`AgentRecord` envelope** per worker rather than a bare agent object.

`AgentRecord` fields:

- `agent_id` — stable, human-readable id (`"CodingAgent-1"`).
- `agent` — the live `BaseAgent` instance (a remote-worker proxy later).
- `capabilities` — mirrored from the agent so the registry can answer capability
  queries without importing concrete classes.
- `status` — `IDLE` / `BUSY` / `OFFLINE`.
- `current_task_id` — what it's running now (preserved when marked offline so the
  Scheduler can reassign).
- `registered_at`, `last_heartbeat` — lifecycle timestamps.

All mutations acquire a single `Lock`. `heartbeat` refreshes `last_heartbeat`
and revives an `OFFLINE` agent to `IDLE`; `set_current_task` atomically flips
BUSY/IDLE; `mark_offline` handles missed-heartbeat eviction.

Rejected: storing raw agents in a dict (no metadata, not remote-ready);
lock-free structures (unnecessary complexity at current scale).

## Consequences

- The Scheduler queries capabilities/status through a stable interface and never
  couples to agent internals.
- The envelope is exactly the shape a distributed registry needs: a
  `RedisAgentRegistry` can persist `AgentRecord`s with a heartbeat **TTL** so
  remote workers self-expire — no Scheduler change.
- Trade-off: a single global lock serialises registry mutations; fine in-memory,
  and sharding/partitioning is a backend concern hidden by the interface.
