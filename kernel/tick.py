from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List

from events.event import Event
from events.event_type import EventType
from kernel.context import KernelContext
from kernel.dispatcher import Dispatcher
from models.enums import AgentStatus
from models.result import AgentResult


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
    results: List[AgentResult] = field(default_factory=list)
    aged_out: List[str] = field(default_factory=list)  # agent_ids marked OFFLINE
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

    1. **assign** — dispatch one wave of pending tasks to idle workers;
    2. **collect events** — drain the results produced this tick;
    3. **update workers** — the heartbeat pass: age out workers whose last
       heartbeat is older than the configured threshold.

    Thinking in discrete ticks (Tick 1 → Tick 2 → …) rather than an opaque busy
    loop makes the runtime deterministic to test and trivial to single-step.
    """

    def __init__(self, context: KernelContext, dispatcher: Dispatcher) -> None:
        self._ctx = context
        self._dispatcher = dispatcher

    def run_once(self, tick_number: int) -> TickResult:
        """Execute one tick and return its :class:`TickResult`."""
        # 1. assign — one dispatch wave.
        dispatched = self._dispatcher.dispatch_available()

        # 2. collect events — results produced this tick.
        results = self._dispatcher.collect_results()

        # 3. update workers — heartbeat aging.
        aged_out = self._update_workers()

        registry = self._ctx.registry
        active = sum(
            1 for r in registry.list_agents() if r.status != AgentStatus.OFFLINE
        )
        pending = len(self._ctx.task_queue.pending_tasks())

        return TickResult(
            tick=tick_number,
            dispatched=dispatched,
            results=results,
            aged_out=aged_out,
            active_workers=active,
            pending_after=pending,
        )

    # ------------------------------------------------------------------ #
    #  Heartbeat (Module 4)
    # ------------------------------------------------------------------ #

    def _update_workers(self) -> List[str]:
        """
        Mark workers OFFLINE when their last heartbeat is stale.

        A worker is considered dead when ``now - last_heartbeat`` exceeds
        ``settings.agent_offline_after_seconds`` while it is still IDLE or BUSY.
        In-memory workers rarely age out (they don't miss heartbeats), so this
        mainly exercises the mechanism that a **distributed** registry will rely
        on — a remote worker that stops pinging self-expires. Reuses
        ``registry.list_agents`` / ``last_heartbeat`` / ``mark_offline``.
        """
        ctx = self._ctx
        threshold = ctx.settings.agent_offline_after_seconds
        now = datetime.now(timezone.utc)
        aged_out: List[str] = []

        for record in ctx.registry.list_agents():
            if record.status not in (AgentStatus.IDLE, AgentStatus.BUSY):
                continue
            age = (now - record.last_heartbeat).total_seconds()
            if age > threshold:
                ctx.registry.mark_offline(record.agent_id)
                aged_out.append(record.agent_id)
                ctx.event_bus.publish(
                    Event(
                        type=EventType.AGENT_OFFLINE,
                        payload={
                            "agent_id": record.agent_id,
                            "reason": "heartbeat_timeout",
                            "age_seconds": age,
                        },
                        source="Kernel.Heartbeat",
                    )
                )
                ctx.logger.warning(
                    "Worker %s aged out after %.1fs without heartbeat.",
                    record.agent_id,
                    age,
                )

        return aged_out
