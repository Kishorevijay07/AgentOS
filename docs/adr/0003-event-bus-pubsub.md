# ADR-0003: In-memory publish/subscribe Event Bus

- **Status:** Accepted
- **Date:** 2026-07-11

## Context

Components must observe each other's activity — a monitor watching task
completions, a logger recording assignments, metrics counting failures — without
the producer (Scheduler, Supervisor, agents) knowing who is listening. Direct
method calls would couple every producer to every consumer and make new
observers invasive to add.

We also need the messaging layer to be swappable for Redis/Kafka later without
rewriting producers or consumers.

## Decision

We will use a publish/subscribe Event Bus behind `AbstractEventBus`
(`subscribe`, `unsubscribe`, `publish`, `history`), with an `InMemoryEventBus`
default:

- **Synchronous delivery** by default — `publish` invokes each handler in the
  caller's thread before returning, making behaviour deterministic and easy to
  test. `publish_async` exists for fire-and-forget (e.g. heartbeats).
- **Handler isolation** — an exception in one subscriber is caught and logged so
  it cannot break others.
- **History ring-buffer** — the last N events are retained for debugging/replay.
- **Thread-safe** — a single `Lock` guards the subscriber map and history.
- Events are immutable `Event` DTOs discriminated by a canonical `EventType`
  enum (`str`-mixin → directly JSON-serialisable).

Rejected: direct observer method calls (tight coupling); starting with an async
message broker (premature — no distribution requirement yet, but the interface
keeps the door open).

## Consequences

- New observers are added by subscribing — zero producer changes.
- A `RedisEventBus` / `KafkaEventBus` implementing `AbstractEventBus` slots in at
  the Kernel with no consumer change (enables cross-node fan-out and durable
  event streams / event sourcing).
- Trade-off: synchronous handlers run in the publisher's thread, so a slow
  subscriber slows the publisher — acceptable in-process; a distributed bus
  removes this coupling.
