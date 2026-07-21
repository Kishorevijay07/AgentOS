from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from kernel.context import KernelContext
from runtime.lifecycle import WorkerState
from runtime.outcome import ExecutionOutcome


@dataclass
class TickResult:
    """
    Immutable summary of a single :class:`Tick` iteration.

    Returning a structured result (instead of nothing) is what makes the runtime
    inspectable: tests and simulations can assert on exactly what one tick did,
    and a debugger/dashboard can render the runtime frame-by-frame.
    """

    tick: int
    dispatched: int
    results: List[ExecutionOutcome] = field(default_factory=list)
    aged_out: List[str] = field(default_factory=list)  # worker_ids marked unhealthy
    active_workers: int = 0
    pending_after: int = 0

    def __repr__(self) -> str:
        return (
            f"TickResult(tick={self.tick}, dispatched={self.dispatched}, "
            f"results={len(self.results)}, aged_out={len(self.aged_out)}, "
            f"active_workers={self.active_workers}, pending_after={self.pending_after})"
        )


class Tick:
    """
    One iteration of the kernel heartbeat — the unit that replaces ``while True``.

    Each tick performs three steps, in order:

    1. **assign** — one scheduler wave: ready tasks placed on capable workers
       (with the local backend a wave executes them synchronously too);
    2. **collect** — drain the outcomes the scheduler reconciled this tick;
    3. **update workers** — the runtime health pass: probe idle workers and mark
       unresponsive ones FAILED.

    Thinking in discrete ticks (Tick 1 → Tick 2 → …) rather than an opaque busy
    loop makes the runtime deterministic to test and trivial to single-step.
    """

    def __init__(self, context: KernelContext) -> None:
        self._ctx = context

    def run_once(self, tick_number: int) -> TickResult:
        """Execute one tick and return its :class:`TickResult`."""
        ctx = self._ctx

        # 1. assign — one placement wave (reaping lost workers first).
        ctx.scheduler.reap_lost_tasks()
        dispatched = ctx.scheduler.schedule_wave()

        # 2. collect — outcomes reconciled since the last drain.
        results = ctx.scheduler.drain_outcomes()

        # 3. update workers — health probe (unresponsive → FAILED).
        aged_out = ctx.worker_runtime.health_check()

        active = sum(
            1 for h in ctx.worker_runtime.all_workers()
            if h.state not in (WorkerState.OFFLINE, WorkerState.FAILED)
        )
        pending = len(ctx.graph.pending_tasks())

        return TickResult(
            tick=tick_number,
            dispatched=dispatched,
            results=results,
            aged_out=aged_out,
            active_workers=active,
            pending_after=pending,
        )
