# ADR-0007: Worker lifecycle state machine

- **Status:** Accepted
- **Date:** 2026-07-11

## Context

Workers are long-lived, like OS processes: they initialise (loading models,
opening connections), go idle, get busy, may pause (rate-limit backoff,
maintenance), and eventually shut down. Without an explicit lifecycle, illegal
transitions (executing a terminated worker, resuming one that was never paused)
slip through, and every agent re-implements the same boilerplate differently.

## Decision

We will model the worker lifecycle as an explicit **state machine** and provide
default behaviour via a mixin:

- **`WorkerState`**: `INITIALIZING → IDLE → BUSY`, with `PAUSED` and a terminal
  `SHUTTING_DOWN → TERMINATED`.
- **`_TRANSITIONS`** whitelists legal moves; `_transition` raises `RuntimeError`
  on any illegal transition. State reads/writes are guarded by a per-worker
  `Lock`.
- **`BaseAgent`** (ABC) declares the full contract: `execute` plus the lifecycle
  hooks `initialize`, `pause`, `resume`, `shutdown`, `heartbeat`.
- **`WorkerMixin`** supplies default implementations (state transitions +
  `AGENT_ONLINE`/`AGENT_OFFLINE`/`AGENT_HEARTBEAT` events). Concrete agents
  inherit `class XAgent(WorkerMixin, BaseAgent)` and override only what they need.
- Registry/bus integration is injected post-construction via `_configure_worker`,
  so heartbeats update the registry and publish events.

Rejected: implicit boolean flags (`is_busy`) (no transition guarantees);
duplicating lifecycle logic in each agent (drift, inconsistency).

## Consequences

- Illegal transitions fail fast with a clear error.
- New agents get correct lifecycle + health/heartbeat behaviour for free.
- The `heartbeat` + registry `last_heartbeat` pairing is exactly what remote
  workers need: a distributed registry can evict on missed heartbeat (TTL).
- Trade-off: the mixin needs `_ensure_worker_state`/`_configure_worker` wiring;
  the Kernel's `register_agent` centralises that so call-sites don't repeat it.
