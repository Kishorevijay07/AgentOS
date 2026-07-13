# ADR-0009: Kernel runtime — context, dispatcher, lifecycle, tick

- **Status:** Accepted
- **Date:** 2026-07-11
- **Supersedes:** the `Supervisor` role from [ADR-0001](0001-layered-architecture-and-kernel-composition-root.md)

## Context

After Sprints 1–2 the `Kernel` was a thin facade that ran a whole batch by
looping `Supervisor.run_once()` inside `while True`. That is enough to execute
tasks but it is not a *runtime*: there is no lifecycle (you cannot pause or stop
it), no notion of a single schedulable step (hard to simulate or debug), the
five collaborators are threaded through every constructor, and unresponsive
workers are never noticed. AgentOS is meant to be the "heartbeat" of the system —
modelled on an OS scheduler / Docker's container lifecycle — so the Kernel needs
the shape of one.

## Decision

We will restructure the Kernel into an OS-style runtime, in a dedicated
`kernel/` package:

- **`KernelContext`** (`context.py`) — a frozen dependency-injection container
  holding the runtime services (event bus, registry, task queue, result queue,
  result store, scheduler, settings, logger), each typed as its abstraction.
  `KernelContext.in_memory(**overrides)` is the single wiring site. Memory/LLM
  services are deliberately excluded until they exist. Modules receive one
  `context` instead of five arguments.
- **`Dispatcher`** (`dispatcher.py`) — the evolution of the `Supervisor`: it
  assigns work, tracks each execution, handles failure, and now publishes the
  full task lifecycle (`TASK_STARTED` → `TASK_COMPLETED`/`TASK_FAILED`). The
  `supervisor/` package is retired and its tests ported.
- **`Lifecycle`** (`lifecycle.py`) — a `KernelState` machine
  (BOOTING→RUNNING→PAUSED→STOPPING→STOPPED) guarded exactly like the worker
  `WorkerState` machine; illegal transitions raise.
- **`Tick`** (`tick.py`) — one iteration = *assign a wave* → *collect results* →
  *update workers (heartbeat aging)*. It returns a `TickResult` snapshot.
  Thinking in discrete ticks (not an opaque `while True`) makes the runtime
  deterministic to test and single-step.
- **`Kernel`** (`kernel.py`) — owns the lifecycle + tick loop and exposes both a
  deterministic `tick()`/`run_until_idle()` and a threaded `run()`/`pause()`/
  `resume()`/`stop()`, plus `health()` for monitoring.

The heartbeat monitor lives inside `tick.py` (the "update workers" step) to match
the intended five-file `kernel/` layout.

## Consequences

- The Kernel is now an always-on, controllable runtime **and** a deterministic
  step machine — simulation and debugging use the same `tick()` the loop uses.
- Task lifecycle events are finally emitted (closing the gap noted in Sprint 2);
  observers get `TASK_STARTED/COMPLETED/FAILED`.
- One `KernelContext` removes constructor-argument sprawl and keeps the
  Redis/Kafka swap seam ([ADR-0008](0008-program-to-abstractions.md)) in exactly
  one place.
- Heartbeat aging is mostly latent for in-memory workers but is the mechanism a
  distributed registry will use to evict silent remote workers.
- Cost accepted: more moving parts and a background thread (kept simple: a
  daemon thread with a `stop_event`-driven responsive sleep).
- A subtle trap surfaced and is now guarded: the queues/registry/store define
  `__len__`, so an empty injected instance is falsy — `KernelContext.in_memory`
  uses explicit `is None` checks, never `x or Default()`.
