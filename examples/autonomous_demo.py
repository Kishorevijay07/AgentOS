"""
AgentOS v0.8 demo — the autonomous loop, end to end.

A real goal is planned by a real LLM, executed by LLM-backed workers, and then
**reflected on**: a real LLM judges each output and can inject corrective /
follow-up tasks into the live graph, which then run — bounded by a hard replan
budget so the loop always terminates.

Setup
-----
1. ``cp .env.example .env`` and set ``OPENROUTER_API_KEY``.
2. ``python examples/autonomous_demo.py "Build a REST API for a blog"``
"""
from __future__ import annotations

import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from agents.coding import CodingAgent
from agents.documentation import DocumentationAgent
from agents.research import ResearchAgent
from agents.testing import TestingAgent
from kernel import Kernel, KernelContext
from models.task import Task
from planning import DefaultPlanningPrompt, LLMPlanner, PlanningService
from planning.models import Goal
from reflection import LLMReflector
from services.openrouter import LLMClientError, OpenRouterLLMClient
from task_queue import TaskQueue


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s  %(message)s")
    goal = sys.argv[1] if len(sys.argv) > 1 else "Build a REST API for a blog"

    try:
        llm = OpenRouterLLMClient.from_env()
    except LLMClientError as exc:
        print(f"\n{exc}\nCopy .env.example to .env and set OPENROUTER_API_KEY.\n")
        return 1

    # Worker pool declared first so both planner and reflector know the
    # capability vocabulary (so every proposed/replanned task is schedulable).
    workers = [ResearchAgent(llm=llm), CodingAgent(llm=llm),
               TestingAgent(), DocumentationAgent()]
    caps = sorted({c for w in workers for c in w.capabilities})

    # Plan the goal with a real model.
    planner = LLMPlanner(llm, prompt=DefaultPlanningPrompt(capability_hint=caps))
    plan = PlanningService(planner, TaskQueue()).plan(Goal(description=goal, max_steps=6))
    print(f"\nInitial plan for {goal!r} — {len(plan)} step(s):")
    for step in plan.ordered_steps():
        print(f"  {step.order}. {step.description}  caps={step.capabilities}")

    # Kernel with reflection enabled — the autonomous loop.
    context = KernelContext.in_memory(
        reflector=LLMReflector(llm),
        goal=goal,
        allowed_capabilities=caps,
        max_replans=4,
    )
    kernel = Kernel(context).boot()
    for worker in workers:
        kernel.register_agent(worker)

    # Seed the plan into the kernel's graph and run the loop.
    for step in plan.ordered_steps():
        kernel.submit(Task(description=step.description,
                           required_capabilities=step.capabilities))
    kernel.run_until_idle()

    # Report — original vs. reflection-injected tasks.
    print("\n" + "=" * 72)
    for node in kernel.graph.nodes():
        tag = "REFLECT" if node.metadata.get("origin") == "reflection" else "PLANNED"
        state = node.state.value
        print(f"  [{tag}:{state:<9}] {node.description[:70]}")
    print(f"\nHealth: {kernel.health()}")
    print(f"Reflection injected {context.reflection.replans_done} corrective task(s).")
    kernel.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
