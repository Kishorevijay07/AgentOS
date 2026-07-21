# ADR-0013: Checkpointing and crash-resume

- **Status:** Accepted
- **Date:** 2026-07-22

## Context

An autonomous run ([ADR-0012](0012-reflection-dynamic-replanning.md)) can be
long and expensive — many LLM calls across a growing task graph. If the process
dies mid-run, everything is lost and the whole goal must restart from scratch,
re-doing (and re-paying for) completed work. A production runtime needs to
**survive a crash and resume where it left off**.

## Decision

We will snapshot a run's **execution state** to a pluggable store, and restore
it into a fresh Kernel.

**What is the execution state?** The task graph — and *only* the task graph plus
two scalars. Every `TaskNode` already carries its lifecycle `state`, its
dependencies/children, and — via `node.history` — the per-attempt execution
record (worker, timing, success/error, `execution_id`). Because `TaskNode` is a
Pydantic model, snapshotting the node list is **lossless and JSON-serialisable
for free**. Add the reflection budget (`replans_done`, so a resumed run can't
exceed its replan cap across the crash) and the tick counter, and the run is
fully reconstructable.

- **`checkpoint/`**: `Checkpoint` (Pydantic: nodes + tick_count + replans_done)
  and a `CheckpointStore` port with `InMemoryCheckpointStore` and
  `FileCheckpointStore`. The file store writes to a temp file and `os.replace`s
  it into place — an **atomic** swap, so a crash mid-write can never corrupt the
  checkpoint (it is always either the old snapshot or the new one).
- **Graph**: `snapshot()` (deep copies) and `restore(nodes)`. On restore, any
  task that was `RUNNING` at snapshot time is reset — its in-flight execution did
  not finish, so it goes back to `READY`/`BLOCKED` and **re-runs**. Completed and
  blocked states are preserved, so finished work is never redone.
- **Kernel**: `checkpoint()`, `save_checkpoint(store)`, `restore(checkpoint)`,
  `load_checkpoint(store)`, plus opt-in auto-save every
  `settings.checkpoint_every_ticks` ticks. A failed auto-checkpoint is logged and
  swallowed — persistence must never crash the run it protects.

Rejected: persisting the whole `ResultStore` (its logs/artifacts carry `Any`
content that isn't cleanly JSON-serialisable, and the node history already holds
the execution record needed to resume — so it would be redundant complexity);
persisting worker/runtime state (workers re-register on restart; only *what work
is done* must survive).

## Consequences

- A run survives a crash: restore into a fresh Kernel (same workers registered),
  call `run_until_idle`, and it finishes without re-doing completed tasks —
  proven by `test_checkpoint_resume.py`.
- The `CheckpointStore` port means file today, Redis/S3/DB tomorrow, with no
  Kernel change (ADR-0008).
- Checkpointing is entirely opt-in; with no store configured the runtime is
  unchanged.
- Known limit: the richer `ResultStore` trace (structured logs, artifacts) is
  not persisted — the per-task `node.history` is. Durable full-trace persistence
  is a future enhancement (naturally lands with a durable graph backend).
