from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from agents.registry import AgentRecord
from events.event import Event
from events.event_type import EventType
from kernel.context import KernelContext
from models.result import AgentResult
from models.task import Task
from result_store import LogLevel


class Dispatcher:
    """
    Execution engine of the Kernel — the evolution of the former ``Supervisor``.

    The Kernel never executes workers itself; it hands work to the Dispatcher,
    and the Dispatcher owns:

    * **assigning** a task to a worker (via the Scheduler),
    * **tracking** the execution as a :class:`~result_store.ExecutionRecord`
      (one per attempt, keyed by ``execution_id``),
    * **publishing** the task lifecycle on the Event Bus
      (``TASK_STARTED`` → ``TASK_COMPLETED`` / ``TASK_FAILED``),
    * **handling failure** — an unhandled worker exception becomes a failed
      ``AgentResult`` and a failed task, never a crashed loop.

    It reads everything it needs from a single :class:`KernelContext`, so the
    Kernel and Tick pass it one object rather than five collaborators.
    """

    def __init__(self, context: KernelContext) -> None:
        self._ctx = context

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def dispatch_next(self) -> bool:
        """
        Assign and execute exactly one pending task.

        Returns
        -------
        bool
            ``True`` if a task was dispatched, ``False`` if the queue was empty
            or no capable worker was available.
        """
        ctx = self._ctx
        task = ctx.task_queue.get_next_task()
        if task is None:
            return False

        record = ctx.scheduler.dispatch(task)
        if record is None:
            ctx.task_queue.fail_task(task.id, "No capable agent available.")
            ctx.logger.warning("No capable worker for task %s; failed.", task.id)
            return False

        self._assign(task, record)
        result = self._execute(task, record)
        # Return the worker to the IDLE pool (symmetric with Scheduler.dispatch).
        ctx.scheduler.release(record.agent_id)
        ctx.result_queue.push(result)

        if result.success:
            ctx.task_queue.complete_task(task.id, str(result.output))
        else:
            ctx.task_queue.fail_task(task.id, result.error or "Unknown error.")

        return True

    def dispatch_available(self) -> int:
        """
        Dispatch one **wave**: keep dispatching until nothing more can be
        assigned right now.  Returns the number of tasks dispatched.

        This is what a single :class:`~kernel.tick.Tick` performs — bounded work
        per tick keeps the loop deterministic and inspectable.
        """
        dispatched = 0
        while self.dispatch_next():
            dispatched += 1
        return dispatched

    def collect_results(self) -> List[AgentResult]:
        """Drain and return all results produced so far (monitoring step)."""
        return self._ctx.result_queue.drain()

    # ------------------------------------------------------------------ #
    #  Private helpers
    # ------------------------------------------------------------------ #

    def _assign(self, task: Task, record: AgentRecord) -> None:
        """Record which worker owns this task."""
        task.assigned_agent = record.agent_id
        task.updated_at = datetime.now(timezone.utc)

    def _execute(self, task: Task, record: AgentRecord) -> AgentResult:
        """
        Run the worker, bracketed by an execution trace and lifecycle events.

        Opens a fresh :class:`ExecutionRecord` (new ``execution_id``), publishes
        ``TASK_STARTED``, runs ``agent.execute`` inside try/except, then closes
        the trace and publishes ``TASK_COMPLETED`` / ``TASK_FAILED``.
        """
        ctx = self._ctx
        agent = record.agent
        agent_id = record.agent_id

        execution = ctx.result_store.start_execution(task.id, agent_id=agent_id)
        ctx.result_store.add_log(
            task.id,
            LogLevel.INFO,
            f"Task started: {task.description!r}",
            source="Dispatcher",
        )
        self._publish(
            EventType.TASK_STARTED, task, agent_id, execution_id=execution.execution_id
        )

        try:
            output = agent.execute(task)

            ctx.result_store.add_log(
                task.id,
                LogLevel.INFO,
                f"Task completed successfully by {agent_id!r}.",
                source="Dispatcher",
            )
            ctx.result_store.finish_execution(task.id, output=output, success=True)
            self._publish(
                EventType.TASK_COMPLETED,
                task,
                agent_id,
                execution_id=execution.execution_id,
                extra={"result": str(output)},
            )
            return AgentResult(
                task_id=task.id,
                agent_name=type(agent).__name__,
                output=output,
                success=True,
            )

        except Exception as exc:  # noqa: BLE001
            error_msg = str(exc)
            ctx.result_store.add_log(
                task.id, LogLevel.ERROR, f"Task failed: {error_msg}", source="Dispatcher"
            )
            ctx.result_store.finish_execution(
                task.id, output=None, success=False, error=error_msg
            )
            self._publish(
                EventType.TASK_FAILED,
                task,
                agent_id,
                execution_id=execution.execution_id,
                extra={"error": error_msg},
            )
            return AgentResult(
                task_id=task.id,
                agent_name=type(agent).__name__,
                output=None,
                success=False,
                error=error_msg,
            )

    def _publish(
        self,
        event_type: EventType,
        task: Task,
        agent_id: str,
        *,
        execution_id,
        extra: dict | None = None,
    ) -> None:
        payload = {
            "task_id": str(task.id),
            "agent_id": agent_id,
            "execution_id": str(execution_id),
        }
        if extra:
            payload.update(extra)
        self._ctx.event_bus.publish(
            Event(type=event_type, payload=payload, source="Dispatcher")
        )
