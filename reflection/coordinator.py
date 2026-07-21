from __future__ import annotations

import logging
from typing import List, Optional
from uuid import UUID

from events.bus import AbstractEventBus
from events.event import Event
from events.event_type import EventType
from models.task import Task
from reflection.models import ReflectionRequest, ReflectionVerdict
from reflection.reflector import Reflector
from result_store import AbstractResultStore
from runtime.outcome import ExecutionOutcome
from task_graph.graph import AbstractTaskGraph


class ReflectionCoordinator:
    """
    Applies reflection to completed work by mutating the **live** task graph —
    the engine of the autonomous plan→execute→reflect→replan loop.

    Given the outcomes a tick produced, it asks the injected :class:`Reflector`
    to judge each *successful* task and, on a ``REPLAN`` verdict, injects the
    proposed follow-up tasks (and their dependency on the reflected task) into
    the running graph. Those new nodes are picked up by the next scheduler wave —
    so a goal can expand itself as understanding improves.

    The reflector stays pure; **every side effect lives here**. That keeps the
    dangerous part — mutating a running graph — in one auditable place with hard
    guard-rails.

    Loop safety (non-negotiable for autonomy)
    -----------------------------------------
    * a global ``max_replans`` budget caps total injected tasks per run;
    * tasks that were *themselves* injected by reflection (node metadata
      ``origin == "reflection"``) are never reflected on again.

    Together these guarantee the loop terminates — reflection can deepen a plan a
    bounded amount, never forever.
    """

    def __init__(
        self,
        graph: AbstractTaskGraph,
        reflector: Reflector,
        *,
        result_store: Optional[AbstractResultStore] = None,
        event_bus: Optional[AbstractEventBus] = None,
        goal: Optional[str] = None,
        allowed_capabilities: Optional[List[str]] = None,
        max_replans: int = 5,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._graph = graph
        self._reflector = reflector
        self._store = result_store
        self._bus = event_bus
        self._goal = goal
        self._allowed = list(allowed_capabilities or [])
        self._max_replans = max_replans
        self._replans_done = 0
        self._log = logger or logging.getLogger("agentos.reflection")

    @property
    def replans_done(self) -> int:
        return self._replans_done

    def restore(self, replans_done: int) -> None:
        """Restore the spent-budget counter from a checkpoint (so a resumed run
        cannot exceed ``max_replans`` across the crash boundary)."""
        self._replans_done = replans_done

    def process(self, outcomes: List[ExecutionOutcome]) -> List[UUID]:
        """
        Reflect on *outcomes* and inject any follow-up tasks. Returns the ids of
        tasks added to the graph this call.
        """
        injected: List[UUID] = []
        for outcome in outcomes:
            if self._replans_done >= self._max_replans:
                break
            if not outcome.success:
                continue  # failed tasks are the scheduler's RetryPolicy's job
            node = self._graph.get_node(outcome.task_id)
            if node is None or node.metadata.get("origin") == "reflection":
                continue  # never reflect on reflection-injected work (loop guard)

            decision = self._reflector.reflect(self._request(node, outcome))
            if decision.verdict != ReflectionVerdict.REPLAN:
                continue

            self._log.info("Reflection replans %s: %s", outcome.task_id, decision.reason)
            for proposed in decision.new_tasks:
                if self._replans_done >= self._max_replans:
                    break
                injected.append(self._inject(proposed, parent_id=outcome.task_id))
        return injected

    # ------------------------------------------------------------------ #
    #  Internals
    # ------------------------------------------------------------------ #

    def _request(self, node, outcome: ExecutionOutcome) -> ReflectionRequest:
        output = outcome.output
        if (output is None or str(output) == "") and self._store is not None:
            record = self._store.get(outcome.task_id)
            output = record.output if record else None
        return ReflectionRequest(
            task_id=outcome.task_id,
            description=node.description,
            output="" if output is None else str(output),
            success=outcome.success,
            error=outcome.error,
            attempt=node.retry_count,
            goal=self._goal,
            allowed_capabilities=self._allowed,
        )

    def _inject(self, proposed, parent_id: UUID) -> UUID:
        task = Task(
            description=proposed.description,
            priority=proposed.priority,
            required_capabilities=list(proposed.capabilities),
        )
        self._graph.add_task(task)
        if proposed.depends_on_parent:
            self._graph.add_dependency(task.id, parent_id)

        node = self._graph.get_node(task.id)
        if node is not None:
            node.metadata["origin"] = "reflection"
            node.metadata["parent"] = str(parent_id)

        self._replans_done += 1
        self._publish(task, parent_id)
        self._log.info("Injected corrective task %s (parent=%s).", task.id, parent_id)
        return task.id

    def _publish(self, task: Task, parent_id: UUID) -> None:
        if self._bus is None:
            return
        self._bus.publish(
            Event(
                type=EventType.TASK_CREATED,
                payload={
                    "task_id": str(task.id),
                    "description": task.description,
                    "required_capabilities": list(task.required_capabilities),
                    "origin": "reflection",
                    "parent": str(parent_id),
                },
                source="Reflection",
            )
        )
