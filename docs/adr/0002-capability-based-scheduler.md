# ADR-0002: Capability-based scheduler

- **Status:** Accepted
- **Date:** 2026-07-11

## Context

The Scheduler must route each task to an appropriate worker. The naive approach —
`if task.type == "code": use CodingAgent` — hard-codes concrete agent classes
into the Scheduler, so every new agent type edits the Scheduler and the Scheduler
cannot serve remote/plugin workers it has never heard of.

Tasks declare `required_capabilities: list[str]`; agents declare
`capabilities: list[str]`. We need a matching policy that uses only these.

## Decision

We will match on **capabilities**, never on concrete types. `Scheduler.dispatch`:

1. Consider only **IDLE** agents (never pre-empt busy workers).
2. **Full match** = agent capabilities are a superset of the task's required
   capabilities.
3. Among full matches, pick the **most specialised** agent — the one with the
   *fewest* total capabilities — leaving generalists free for tasks that need
   them.
4. If no full match exists, fall back to the agent with the highest **partial
   overlap** (most required capabilities satisfied).
5. On success, mark the agent BUSY via the registry and publish `TASK_ASSIGNED`.

The Scheduler depends on `AbstractAgentRegistry` and `AbstractEventBus` only.

Rejected: round-robin / FIFO (ignores suitability); type-name dispatch (couples
to concrete classes); a full constraint solver (over-engineered for current
scale).

## Consequences

- New agent types require **zero** Scheduler changes — they just declare
  capabilities.
- Remote/plugin workers are schedulable as soon as they register capabilities.
- "Most specialised wins" preserves generalist capacity under load.
- Trade-offs accepted: matching is O(agents × caps) per dispatch (fine
  in-memory; revisit with an index for large fleets), and it currently ignores
  load/cost/locality — future policies (weighted, cost-aware) fit behind the
  same `dispatch` seam without touching callers.
