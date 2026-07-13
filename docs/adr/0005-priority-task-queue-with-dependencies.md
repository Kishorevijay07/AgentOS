# ADR-0005: Priority task queue with dependencies

- **Status:** Accepted
- **Date:** 2026-07-11

## Context

Work arrives faster than it can be executed and is not equally urgent; some
tasks must not start before others complete (e.g. *implement* before *test*).
The runtime needs a queue that orders by priority, respects dependencies and
required capabilities, and survives failures via retry — all behind an interface
that a Redis/Kafka backend can later satisfy unchanged.

## Decision

We will use two interface-backed in-memory queues:

- **`AbstractTaskQueue` / `TaskQueue`** — the pending work store. It maintains
  distinct buckets (pending, in-progress, completed, failed, cancelled) and:
  - orders pending tasks by `Priority` (`critical > high > medium > low`);
  - offers `get_next_for_agent(agent)` for **capability- and dependency-aware**
    dispatch (a task is eligible only when every dependency id is completed and
    the agent's capabilities cover the requirements);
  - supports `retry_task` (re-queue + increment `retry_count`), `cancel_task`,
    and `overdue_tasks` (deadline-aware).
- **`AbstractResultQueue` / `ResultQueue`** — decouples workers from the
  Supervisor: workers `push` an `AgentResult`, the Supervisor `drain`s them.
  Sharing only this queue is what makes workers and the Supervisor horizontally
  separable.

Both guard state with a `Lock`. Method signatures are backend-agnostic.

Rejected: a single `heapq` (doesn't model dependencies/lifecycle buckets); mixing
results back through the task queue (couples workers to Supervisor internals).

## Consequences

- Priorities, dependencies, retries, and deadlines are first-class.
- The two-queue split is the seam for distributed execution (Redis lists /
  streams, Kafka topics) with no consumer change.
- Trade-off: re-sorting the pending deque on insert is O(n log n) — negligible at
  current scale; a heap/priority index is a backend optimisation.
- Note: the Supervisor currently pulls via `get_next_task` (priority order);
  moving it to `get_next_for_agent` upgrades to fully dependency-gated dispatch
  without an interface change.
