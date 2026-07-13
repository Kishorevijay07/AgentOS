# ADR-0008: Program to abstractions (interface per swappable module)

- **Status:** Accepted
- **Date:** 2026-07-11

## Context

AgentOS's stated goal is that **every module be replaceable without affecting the
rest** — the in-memory queue today becomes a Redis queue tomorrow and nothing
else changes. Early on this was applied unevenly: `AbstractEventBus` and
`BaseAgent` were interface-backed, but `TaskQueue`, `ResultQueue`, `ResultStore`,
and `AgentRegistry` were concrete-only, so the Scheduler and Supervisor imported
concrete implementations directly. Worse, `ResultStore`'s docstring advertised an
`AbstractResultStore` that did not exist — the swap seam was documented but not
real. This blocks the Redis/Kafka and distributed-worker roadmap.

## Decision

We will define an interface for **every** swappable subsystem and depend on the
interface everywhere except the composition root:

- Add `AbstractAgentRegistry`, `AbstractTaskQueue`, `AbstractResultQueue`,
  `AbstractResultStore`, each co-located with its in-memory implementation
  (mirroring the existing `AbstractEventBus`) and exported from its package.
- Each ABC declares **only** the methods its consumers use (Interface
  Segregation), not the concrete's full surface.
- Retarget `Scheduler`, `Supervisor`, and `Kernel` type hints to the
  abstractions (Dependency Inversion).
- The `Kernel` is the **sole** place concretes are chosen; it accepts each
  collaborator as an optional injected abstraction defaulting to the in-memory
  concrete — so `Kernel(task_queue=RedisTaskQueue(...))` swaps one subsystem with
  no other change.

Rejected: leaving some modules concrete "until we need Redis" (the seams must
exist *before* the migration, or the migration touches every consumer).

## Consequences

- The Redis/Kafka/distributed migration is now a construction-site change in the
  Kernel; Scheduler/Supervisor/agents are untouched. See the mapping table in
  [`ARCHITECTURE.md` §7](../ARCHITECTURE.md).
- Tests can inject fakes for any subsystem; a fake `AbstractEventBus` verifies
  the swap seam.
- The `ResultStore` docstring is now truthful — the advertised interface exists.
- Cost accepted: more small ABC classes and the discipline that the only place
  allowed to name a concrete implementation is `kernel/`. ABCs hold no logic, so
  concretes remain the single source of behaviour.
