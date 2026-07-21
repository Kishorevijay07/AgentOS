# ADR-0011: Unified scheduler with dispatch backends

- **Status:** Accepted
- **Date:** 2026-07-13
- **Supersedes:** the ``Dispatcher`` from [ADR-0009](0009-kernel-runtime.md)

## Context

By v0.6 AgentOS had accumulated **three** overlapping execution loops, each
re-implementing "pick work, place it, reconcile the result":

1. the Kernel's ``Dispatcher`` (queue + legacy registry, inline execution);
2. the in-process ``ExecutionScheduler`` (graph + worker runtime);
3. the ``DistributedScheduler`` (graph + directory + transport).

Loops 2 and 3 were near-copies differing only in *how a task reaches a worker*;
loop 1 lived in an older queue-based world entirely. Triplicated placement and
retry logic meant every policy change had to land three times, and the Kernel —
the runtime's front door — was not even driving the best engine.

## Decision

We will keep exactly **one** scheduler and move the "how work travels" question
behind a strategy port:

- **`DispatchBackend`** (`scheduling/backend.py`): `candidates()` (capability
  view of free workers), `dispatch(task, worker_id, …)` (fire-and-forget), an
  outcome callback, and `lost_tasks()` (crash detection). Two implementations:
  - `LocalDispatchBackend` — executes synchronously on the in-process
    `AbstractWorkerRuntime`; the outcome is delivered before `dispatch` returns;
  - `TransportDispatchBackend` — publishes a `TaskMessage`; the outcome arrives
    later as a `ResultMessage`.
- **`ExecutionScheduler`** becomes the single placement/reconciliation loop,
  backend-agnostic: ready tasks → capability match (excluding in-flight
  workers) → backend dispatch → reconcile `ExecutionOutcome` into the graph
  (complete / retry / fail), with lost-worker reaping. `CapabilityMatcher` was
  generalised to a `HasCapabilities` protocol so local `WorkerHandle`s and
  remote `RemoteWorkerInfo` records are interchangeable candidates.
- **`DistributedScheduler`** shrinks to a constructor-convenience subclass
  (transport backend pre-wired). No behaviour of its own.
- **The Kernel moves onto the graph runtime**: `KernelContext` now carries
  `graph + worker_runtime + scheduler` (the queue/registry/`Dispatcher` trio is
  retired), `submit` seeds the graph, and each `Tick` is one scheduler wave +
  outcome drain + worker health pass. The `AbstractTaskQueue` seeding surface
  survives via the `GraphTaskQueue` adapter (`kernel.task_queue`).

Rejected: keeping parallel schedulers "for safety" (every fix ×3); making the
transport case a mode-flag inside one class (hidden branching instead of a
seam).

## Consequences

- Placement, retry, reaping, tracing, and event publishing exist **once**;
  local vs. distributed is now a one-argument choice, and the scheduler cannot
  tell whether a worker is in-process or on another machine.
- The Kernel finally drives the same engine the distributed runtime uses —
  timeouts, isolation, and metrics apply to kernel-run work too.
- Unplaceable tasks now stay `READY` instead of being spuriously failed (the
  old Dispatcher failed them); they run as soon as a capable worker appears.
- Cost accepted: `scheduler/scheduler.py` + `agents/registry.py` remain as a
  deprecated legacy layer (still used by `WorkerMixin` integration and old
  tests) pending removal in a cleanup pass.
