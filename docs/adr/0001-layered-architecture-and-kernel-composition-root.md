# ADR-0001: Layered architecture and Kernel composition root

- **Status:** Accepted
- **Date:** 2026-07-11

## Context

AgentOS is an agent *runtime*, modelled on an OS kernel: it must schedule
workers, queue tasks, dispatch work, publish events, and trace execution — and
eventually run distributed workers over Redis/Kafka. If components construct and
reference each other directly, the system fuses into a ball of mud: the
Scheduler ends up importing concrete queues, agents import the bus, and nothing
can be tested or replaced in isolation.

We need clear layers with dependencies pointing inward, and a single place that
assembles the object graph.

## Decision

We will use a clean, layered architecture with a dedicated **Kernel** as the
composition root:

- **Data layer** (`models/`) — shared types with no behaviour and no
  dependencies on other modules.
- **Capability layer** (`events/`, `task_queue/`, `agents/`, `result_store/`) —
  independent subsystems, each behind an interface.
- **Orchestration layer** (`scheduler/`, `supervisor/`) — depends only on the
  capability-layer *abstractions*.
- **Composition root** (`kernel/`) — the only module that imports concrete
  implementations and injects them (see [ADR-0008](0008-program-to-abstractions.md)).

The `Kernel` is a thin facade (`register_agent`, `submit`, `run_once`,
`run_until_empty`, `boot`, `shutdown`) that owns wiring and lifecycle but no
domain logic. `build_kernel()` returns a fully wired in-memory instance.

Rejected: a global singleton service locator (hides dependencies, hard to test)
and constructing components ad hoc at each call-site (duplicates wiring, defeats
swappability).

## Consequences

- Any module can be unit-tested by injecting fakes; integration is exercised
  through the Kernel.
- Swapping a subsystem for a distributed backend is localised to the Kernel.
- The `Kernel` facade gives applications and the future `api/` adapter one
  stable surface to depend on.
- Cost: one extra layer of indirection and the discipline of never importing a
  concrete outside `kernel/`.
