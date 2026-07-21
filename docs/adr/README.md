# Architecture Decision Records (ADRs)

An ADR captures a single significant architectural decision: its context, the
decision itself, and the consequences. We keep them so that six months from now
we remember *why* AgentOS is shaped the way it is — not just *what* the code does.

## Conventions

- One decision per file, numbered `NNNN-kebab-title.md`.
- Never edit an accepted ADR's decision to reflect a change of mind — instead
  add a **new** ADR that supersedes it, and mark the old one `Superseded by ADR-NNNN`.
- Status is one of: `Proposed`, `Accepted`, `Superseded`, `Deprecated`.
- Use [`0000-template.md`](0000-template.md) as the starting point.

## Index

| ADR | Title | Status |
|---|---|---|
| [0001](0001-layered-architecture-and-kernel-composition-root.md) | Layered architecture and Kernel composition root | Accepted |
| [0002](0002-capability-based-scheduler.md) | Capability-based scheduler | Accepted |
| [0003](0003-event-bus-pubsub.md) | In-memory publish/subscribe Event Bus | Accepted |
| [0004](0004-agent-registry-design.md) | Thread-safe Agent Registry | Accepted |
| [0005](0005-priority-task-queue-with-dependencies.md) | Priority task queue with dependencies | Accepted |
| [0006](0006-result-store-execution-trace.md) | Result Store as execution trace | Accepted |
| [0007](0007-worker-lifecycle-state-machine.md) | Worker lifecycle state machine | Accepted |
| [0008](0008-program-to-abstractions.md) | Program to abstractions (interface per swappable module) | Accepted |
| [0009](0009-kernel-runtime.md) | Kernel runtime — context, dispatcher, lifecycle, tick | Accepted |
| [0010](0010-execution-id-per-attempt.md) | Execution id per attempt | Accepted |
| [0011](0011-unified-scheduler-dispatch-backends.md) | Unified scheduler with dispatch backends | Accepted |
| [0012](0012-reflection-dynamic-replanning.md) | Reflection and dynamic replanning (the autonomous loop) | Accepted |
| [0013](0013-checkpointing-and-resume.md) | Checkpointing and crash-resume | Accepted |
