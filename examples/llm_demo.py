"""
AgentOS v0.6 demo — real intelligence, end to end.

A real goal is decomposed by a real LLM (via OpenRouter), compiled into a task
DAG, and executed by LLM-backed workers — all through the same runtime the test
suite exercises offline.

Setup
-----
1. ``cp .env.example .env`` and set ``OPENROUTER_API_KEY`` (and optionally
   ``OPENROUTER_MODEL``).
2. ``python examples/llm_demo.py "Build a REST API for a blog"``

Costs: one planning call + one call per generated task, on the model you chose.
"""
from __future__ import annotations

import logging
import pathlib
import sys

# Allow `python examples/llm_demo.py` from the repo root (examples/ is not a package).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from agents.coding import CodingAgent
from agents.documentation import DocumentationAgent
from agents.research import ResearchAgent
from agents.testing import TestingAgent
from planning import DefaultPlanningPrompt, LLMPlanner, PlanningService
from planning.models import Goal
from result_store import ResultStore
from runtime import DefaultWorkerRuntime
from scheduling import ExecutionScheduler
from services.openrouter import LLMClientError, OpenRouterLLMClient
from task_graph import InMemoryTaskGraph, PlanGraphBuilder
from task_queue import TaskQueue


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s  %(message)s")
    goal = sys.argv[1] if len(sys.argv) > 1 else "Build a REST API for a blog"

    # 1. The one place a concrete LLM is chosen (everything else sees the port).
    try:
        llm = OpenRouterLLMClient.from_env()
    except LLMClientError as exc:
        print(f"\n{exc}\nCopy .env.example to .env and set OPENROUTER_API_KEY.\n")
        return 1

    # 2. The worker pool we're going to run — declared first so the planner can
    #    be told exactly which capability tags exist. Without this hint the
    #    model invents its own tags and nothing can be scheduled.
    workers = [ResearchAgent(llm=llm), CodingAgent(llm=llm),
               TestingAgent(), DocumentationAgent()]
    pool_capabilities = sorted({cap for w in workers for cap in w.capabilities})

    # 3. Plan the goal with the real model (validated before anything runs).
    planner = LLMPlanner(llm, prompt=DefaultPlanningPrompt(capability_hint=pool_capabilities))
    service = PlanningService(planner, TaskQueue())
    plan = service.plan(Goal(description=goal, max_steps=8))
    print(f"\nPlan for {goal!r} — {len(plan)} step(s):")
    for step in plan.ordered_steps():
        deps = f"  (after {step.depends_on})" if step.depends_on else ""
        print(f"  {step.order}. {step.description}  caps={step.capabilities}{deps}")

    # 4. Compile to an executable DAG.
    graph = PlanGraphBuilder().build(plan, graph=InMemoryTaskGraph())

    # 5. Register the pool (coding + research are LLM-backed).
    runtime = DefaultWorkerRuntime(default_timeout=120.0)
    for agent in workers:
        runtime.register_worker(agent)

    # 6. Execute the DAG.
    store = ResultStore()
    ExecutionScheduler(graph, runtime, result_store=store).run_until_idle()

    # 7. Report.
    print("\n" + "=" * 72)
    for record in store.all():
        status = "OK " if record.success else "FAIL"
        preview = str(record.output)[:200].replace("\n", " ")
        print(f"[{status}] {record.agent_id:<22} {preview}")
    done, total = len(graph.completed_tasks()), len(graph.nodes())
    print(f"\nCompleted {done}/{total} tasks.")

    unplaced = [n for n in graph.ready_tasks()]
    if unplaced:
        print("\nCould not be placed (no worker has these capabilities):")
        for node in unplaced:
            print(f"  - {node.description}  needs={node.required_capabilities}")
        print(f"  Pool capabilities: {pool_capabilities}")

    runtime.shutdown()
    return 0 if done == total else 2


if __name__ == "__main__":
    raise SystemExit(main())
