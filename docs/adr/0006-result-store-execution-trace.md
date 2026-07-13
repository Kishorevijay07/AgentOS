# ADR-0006: Result Store as execution trace

- **Status:** Accepted
- **Date:** 2026-07-11

## Context

`task.result` holds a one-line summary of an execution, which is not enough to
answer "what actually happened when task X ran?" — how long it took, what the
worker logged, what artifacts it produced, why it failed. Operators and future
tooling (dashboards, debugging, audits, checkpointing) need a rich, queryable
record, and it must be swappable for a durable backend later.

## Decision

We will keep a dedicated **`ResultStore`** (behind `AbstractResultStore`) that
maps each `TaskID` to an `ExecutionRecord`, kept separate from `task.result`:

- `start_execution(task_id, agent_id)` opens a record (start time, open state);
  `finish_execution(...)` closes it (end time, success, output, error) and
  exposes `duration_seconds`.
- `add_log(level, message, source)` appends structured `LogEntry` lines;
  `add_artifact(name, content, media_type)` attaches named outputs.
- Query surface: `get`, `all`, `successful`, `failed`, `open_executions`, and a
  flexible `query(agent_id, since, success)`.

The Supervisor brackets every `agent.execute` with
`start_execution`/`finish_execution` and logs start/completion/failure, so the
trace is produced automatically. Integration is additive — omitting the store
leaves all other behaviour unchanged.

Rejected: overloading `task.result` (loses structure/timing/logs); writing
straight to a logging framework (not queryable as first-class records).

## Consequences

- Every run has a complete, inspectable history without agents doing extra work.
- `ExecutionRecord` is the natural unit for **checkpointing** and, combined with
  the event log, for reconstructing state.
- A `SQLResultStore` / `RedisResultStore` implementing `AbstractResultStore`
  gives durable, shared, queryable traces with no consumer change.
- Trade-off: in-memory records grow unbounded — a durable backend with retention
  is the intended production form.
