# ADR-0012: Reflection and dynamic replanning (the autonomous loop)

- **Status:** Accepted
- **Date:** 2026-07-22

## Context

Through v0.7 AgentOS was a **one-shot pipeline**: plan → execute → stop. It never
inspected what workers produced, so a shallow or wrong output ended the run just
as "successfully" as a good one. A real agent runtime needs a feedback loop —
judge the work, and if it falls short, do more.

The task graph was built for this from the start: `add_task`, `add_dependency`
(cycle-guarded), and per-node `metadata` are thread-safe *runtime* mutation
primitives ([ADR-0009](0009-kernel-runtime.md)). What was missing was a
component to decide *when* and *what* to add, and a safe place to do it.

## Decision

We will add a `reflection/` subsystem — planning's mirror image — and wire it
into the Kernel tick as an **opt-in** step:

- **`Reflector`** (strategy): `reflect(ReflectionRequest) -> ReflectionDecision`
  (`ACCEPT` | `REPLAN` + proposed follow-up tasks). Two implementations:
  `HeuristicReflector` (deterministic, offline) and `LLMReflector` (model-graded).
  A reflector is **pure** — it judges and returns a decision; it never touches
  the graph.
- **`ReflectionCoordinator`** owns *every* side effect. Given a tick's outcomes,
  for each **successful** task it runs the reflector and, on `REPLAN`, injects
  the proposed tasks into the **live** graph (`add_task` + `add_dependency` on
  the reflected task) and stamps `metadata.origin = "reflection"`.
- **Kernel integration**: `KernelContext.in_memory(reflector=…)` builds a
  coordinator; each `Tick` runs it after draining outcomes, so injected tasks
  are picked up by the next scheduler wave. With no reflector the behaviour is
  byte-for-byte the pre-v0.8 one-shot pipeline.

**Fail-open everywhere.** Reflection is advisory: a failed LLM call or
unparseable output degrades to `ACCEPT`. A flaky reflector can never stall or
crash a run.

**Loop safety (the crux).** Autonomy that can add its own work must be bounded:
1. a global `max_replans` budget caps total injected tasks per run;
2. tasks already injected by reflection (`origin == "reflection"`) are never
   reflected on again.

Together these guarantee termination — reflection can deepen a plan a bounded
amount, never forever.

Rejected: reflecting inside the scheduler's `_on_outcome` (couples placement to
judgement, and mutating the graph mid-reconciliation is hazardous); letting the
reflector mutate the graph directly (spreads the dangerous side effect and makes
reflectors untestable); failing the run on a bad reflection (advisory work must
never be load-bearing).

## Consequences

- AgentOS is genuinely autonomous: plan → execute → reflect → replan → execute,
  until the work is done or the budget is spent. A goal expands itself.
- Reflection is backend-agnostic (it operates on graph + outcomes), so it works
  under the local and distributed schedulers alike.
- The legacy `agents/reflection.py` `ReflectionAgent` (queue-shaped, unused by
  the new path) is superseded by `HeuristicReflector`.
- Cost accepted: an LLM-graded loop spends extra model calls; the budget bounds
  both runaway loops and cost.
