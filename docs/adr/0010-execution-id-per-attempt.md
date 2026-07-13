# ADR-0010: Execution id per attempt

- **Status:** Accepted
- **Date:** 2026-07-11

## Context

A task can run more than once — it may fail and be retried
([ADR-0005](0005-priority-task-queue-with-dependencies.md) gives
`TaskQueue.retry_task`). The execution trace
([ADR-0006](0006-result-store-execution-trace.md)) captured each run as an
`ExecutionRecord`, but the `ResultStore` keyed those records by `task_id`. So a
retry **overwrote** the previous attempt: the history of "attempt 1 failed with
X, attempt 2 succeeded" was lost, which undermines debugging, auditing, and any
future checkpointing/event-sourcing.

## Decision

We will make the **execution**, not the task, the unit of identity:

- `ExecutionRecord` gains an `execution_id: UUID` (unique per attempt), added in
  the defaults block so existing keyword/positional construction is unaffected.
- `ResultStore` keys records by `execution_id` internally, with two secondary
  indexes: `task_id → [execution_id, …]` (attempt history, ordered) and
  `task_id → open execution_id` (the currently-running attempt).
- The existing `task_id`-facing API is preserved: `start_execution` opens a new
  attempt (still refusing a second *simultaneously open* one), while `add_log`
  and `finish_execution` act on the task's currently-open attempt. `get(task_id)`
  returns the **latest** attempt.
- New accessors — `get_by_execution(execution_id)` and
  `executions_for(task_id)` — are added to both `AbstractResultStore` and the
  concrete store so the swap contract stays complete.

Rejected: a separate parallel `Execution` model/store (duplicates the timing,
logs, and metrics `ExecutionRecord` already has); keeping `task_id` keying
(loses retry history).

## Consequences

- Retried tasks now produce distinct, independently-inspectable records; the
  `Dispatcher` opens one execution per attempt and stamps its `execution_id`
  onto the `TASK_STARTED/COMPLETED/FAILED` events.
- The change is backward-compatible: all pre-existing `ResultStore` tests pass
  unchanged, because the `task_id`-facing methods behave identically for the
  single-attempt case.
- `len(store)` now counts executions rather than tasks — identical when each
  task runs once, and more accurate when they don't.
- Trade-off: two extra in-memory indexes per store; negligible, and a durable
  backend would model the same as a table keyed by `execution_id`.
