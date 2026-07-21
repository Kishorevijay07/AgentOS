"""
Integration test — the autonomous loop end to end through the Kernel.

A goal is executed; reflection judges the output; a poor result triggers a
dynamic replan (a corrective task injected into the live graph) which then runs;
the loop terminates within the replan budget (never spins).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List

import pytest

from kernel import Kernel, KernelContext
from models.task import Task
from reflection.reflector import HeuristicReflector
from task_graph.state import NodeState


class _Worker:
    capabilities: List[str] = ["code"]

    def __init__(self, output: str) -> None:
        self._output = output
        self.runs = 0

    def initialize(self) -> None: ...
    def execute(self, task: Task) -> Any:
        self.runs += 1
        return self._output
    def heartbeat(self) -> datetime:
        return datetime.now(timezone.utc)
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def shutdown(self) -> None: ...


def _kernel(worker: _Worker, **reflect_kwargs) -> Kernel:
    ctx = KernelContext.in_memory(
        reflector=HeuristicReflector(),
        allowed_capabilities=["code"],
        **reflect_kwargs,
    )
    kernel = Kernel(ctx).boot()
    kernel.register_agent(worker)
    return kernel


class TestAutonomousLoop:
    def test_poor_output_triggers_one_replan_then_terminates(self):
        # "ok" is below the heuristic quality bar → reflection replans once.
        worker = _Worker(output="ok")
        kernel = _kernel(worker, max_replans=3)

        kernel.submit(Task(description="build the thing", required_capabilities=["code"]))
        outcomes = kernel.run_until_idle()

        # Original + one injected corrective task both ran; loop stopped.
        assert kernel.context.reflection.replans_done == 1
        assert len(kernel.graph.completed_tasks()) == 2
        assert worker.runs == 2
        assert kernel.graph.has_active_work() is False
        assert kernel.health()["replans"] == 1

        # The injected task carries reflection provenance.
        injected = [n for n in kernel.graph.nodes()
                    if n.metadata.get("origin") == "reflection"]
        assert len(injected) == 1
        assert injected[0].state == NodeState.COMPLETED
        kernel.shutdown()

    def test_good_output_does_not_replan(self):
        worker = _Worker(output="a thorough, complete, high quality answer")
        kernel = _kernel(worker)

        kernel.submit(Task(description="build", required_capabilities=["code"]))
        kernel.run_until_idle()

        assert kernel.context.reflection.replans_done == 0
        assert len(kernel.graph.completed_tasks()) == 1
        assert worker.runs == 1
        kernel.shutdown()

    def test_budget_bounds_the_replans(self):
        # Three poor original tasks would each want a corrective, but the budget
        # caps total injections — proving the loop is bounded.
        worker = _Worker(output="no")
        kernel = _kernel(worker, max_replans=2)

        for i in range(3):
            kernel.submit(Task(description=f"task {i}", required_capabilities=["code"]))
        kernel.run_until_idle()

        # 3 originals + 2 injected (budget hit) = 5 completed, then it stops.
        assert kernel.context.reflection.replans_done == 2
        assert len(kernel.graph.completed_tasks()) == 5
        assert kernel.graph.has_active_work() is False
        kernel.shutdown()


class TestBackwardCompatible:
    def test_kernel_without_reflector_is_unchanged(self):
        from kernel import build_kernel

        kernel = build_kernel().boot()
        assert kernel.context.reflection is None
        assert kernel.health()["replans"] == 0
        kernel.shutdown()
