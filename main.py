"""
AgentOS — runnable reference composition.

Boots the :class:`~kernel.kernel.Kernel`, registers the concrete agents,
submits a small task graph, turns the execution loop until the queue drains,
and prints the resulting execution trace and event history.

Run it::

    python main.py
"""
from __future__ import annotations

import logging

from agents.coding import CodingAgent
from agents.documentation import DocumentationAgent
from agents.research import ResearchAgent
from agents.testing import TestingAgent
from kernel import build_kernel
from models.enums import Priority
from models.task import Task


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    _configure_logging()

    # 1. Compose the runtime (all in-memory; swap a backend by injecting it here).
    kernel = build_kernel().boot()

    # 2. Admit workers to the pool.  Each fires AGENT_ONLINE on initialize().
    for agent in (CodingAgent(), ResearchAgent(), TestingAgent(), DocumentationAgent()):
        kernel.register_agent(agent)

    # 3. Submit a task graph.  The current Supervisor pulls in priority order,
    #    so we encode ordering via priority; `dependencies` is illustrative of
    #    the Task model's dependency-aware dispatch (see get_next_for_agent).
    research = Task(
        description="Research the target problem domain",
        required_capabilities=["research"],
        priority=Priority.CRITICAL,
    )
    build = Task(
        description="Implement the feature",
        required_capabilities=["code"],
        priority=Priority.HIGH,
        dependencies=[research.id],
    )
    verify = Task(
        description="Write and run tests for the feature",
        required_capabilities=["test"],
        priority=Priority.MEDIUM,
        dependencies=[build.id],
    )
    document = Task(
        description="Document the feature",
        required_capabilities=["document"],
        priority=Priority.LOW,
        dependencies=[build.id],
    )
    for task in (research, build, verify, document):
        kernel.submit(task)

    # 4a. Deterministic stepping — advance one tick and inspect the frame.
    print("\n" + "=" * 70)
    print("Manual ticks (deterministic stepping):")
    first_tick = kernel.tick()
    print(f"  {first_tick}")

    # 4b. Threaded loop — let the background heartbeat drain the rest.
    kernel.run()
    while not kernel.task_queue.is_empty():
        pass  # the run loop is ticking in the background
    kernel.stop()

    # 5. Report the execution trace.
    print("\nExecution trace (ResultStore):")
    for record in kernel.store.all():
        status = "OK " if record.success else "FAIL"
        dur = record.duration_seconds or 0.0
        exec_short = str(record.execution_id)[:8]
        print(
            f"  [{status}] {record.agent_id:<20} exec={exec_short} "
            f"{dur*1000:6.2f} ms  -> {record.output!r}"
        )

    print("\nTask lifecycle events:")
    for event in kernel.bus.history():
        if event.type.value.startswith("task."):
            print(f"  {event.type.value:<16} from {event.source}")

    print(f"\nHealth: {kernel.health()}")
    print("=" * 70)

    # 6. Clean shutdown — fires AGENT_OFFLINE per worker; kernel → STOPPED.
    kernel.shutdown()
    print(f"Final state: {kernel.state.value}")


if __name__ == "__main__":
    main()
