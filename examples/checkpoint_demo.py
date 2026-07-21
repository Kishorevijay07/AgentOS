"""
AgentOS v0.9 demo - crash and resume.

Runs a task DAG partway, saves a checkpoint, throws the kernel away (simulating
a crash), then builds a fresh kernel, restores the checkpoint, and finishes -
without re-doing completed work. No API key needed (uses placeholder workers).

    python examples/checkpoint_demo.py
"""
from __future__ import annotations

import logging
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from agents.coding import CodingAgent
from agents.research import ResearchAgent
from checkpoint import FileCheckpointStore
from kernel import build_kernel
from models.task import Task
from task_graph.state import NodeState


def _states(kernel) -> str:
    return ", ".join(
        f"{n.description}={n.state.value}" for n in sorted(kernel.graph.nodes(),
                                                           key=lambda n: n.description)
    )


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(name)s  %(message)s")
    path = pathlib.Path(tempfile.gettempdir()) / "agentos_demo.checkpoint.json"
    store = FileCheckpointStore(path)

    # A small pipeline: research → code → test (linear dependencies).
    research = Task(description="research", required_capabilities=["research"])
    code = Task(description="code", required_capabilities=["code"],
                dependencies=[research.id])
    test = Task(description="test", required_capabilities=["code"],
                dependencies=[code.id])

    # --- Run 1: do a couple of ticks, then "crash". ---
    print("-- Run 1 " + "-" * 45)
    k1 = build_kernel().boot()
    k1.register_agent(ResearchAgent())
    k1.register_agent(CodingAgent())
    for t in (research, code, test):
        k1.submit(t)

    k1.tick()  # research runs, code unlocks
    k1.tick()  # code runs, test unlocks
    k1.save_checkpoint(store)
    print(f"  states: {_states(k1)}")
    print(f"  checkpoint written to {path.name} - simulating crash.")
    k1.shutdown()

    # --- Run 2: fresh process, restore, finish. ---
    print("-- Run 2 (fresh kernel) " + "-" * 30)
    k2 = build_kernel().boot()
    k2.register_agent(ResearchAgent())
    k2.register_agent(CodingAgent())
    resumed = k2.load_checkpoint(store)
    print(f"  resumed from checkpoint: {resumed}")
    print(f"  states on restore: {_states(k2)}")

    k2.run_until_idle()
    print(f"  states after finish: {_states(k2)}")

    done = len(k2.graph.completed_tasks())
    ok = done == 3 and all(n.state == NodeState.COMPLETED for n in k2.graph.nodes())
    print(f"\n  Completed {done}/3 tasks - resume {'OK' if ok else 'FAILED'}.")
    k2.shutdown()
    path.unlink(missing_ok=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
