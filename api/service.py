from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import UUID, uuid4

from agents.coding import CodingAgent
from agents.documentation import DocumentationAgent
from agents.research import ResearchAgent
from agents.testing import TestingAgent
from api.models import CreatedRun, RunSummary, TaskView, TraceView
from kernel import Kernel, KernelContext
from models.task import Task
from planning import DefaultPlanningPrompt, LLMPlanner, PlanningService, TemplatePlanner
from planning.models import Goal
from reflection import LLMReflector
from services.llm import LLMClient
from task_queue import TaskQueue

logger = logging.getLogger("agentos.api")


@dataclass
class Run:
    """A single goal execution: its own Kernel, graph, workers, and thread."""

    run_id: UUID
    goal: str
    kernel: Kernel
    plan: List[str]
    status: str = "running"          # running | completed | failed
    error: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    thread: Optional[threading.Thread] = None


class RunManager:
    """
    Manages many concurrent goal executions behind the HTTP API.

    Each goal becomes a self-contained :class:`Run` — its own Kernel (graph +
    worker pool + event bus + result store) executing in a daemon thread. The
    manager is the thin application service the API routes call; it holds no
    domain logic itself, only lifecycle and lookup.

    Intelligence is automatic: if an :class:`LLMClient` is supplied (the app
    builds one from ``OPENROUTER_API_KEY`` when present) planning, reflection,
    and the coding/research workers are LLM-backed; otherwise the deterministic
    ``TemplatePlanner`` and placeholder workers keep the service fully runnable
    with no API key.
    """

    def __init__(self, *, llm: Optional[LLMClient] = None) -> None:
        self._llm = llm
        self._runs: Dict[UUID, Run] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    #  Run lifecycle
    # ------------------------------------------------------------------ #

    def create_run(self, goal: str, *, max_steps: int = 8, max_replans: int = 0) -> CreatedRun:
        """Plan *goal*, seed a kernel, and start executing it in the background."""
        workers = self._build_workers()
        caps = sorted({c for w in workers for c in w.capabilities})

        # Plan synchronously so the response can carry the plan.
        planner = (
            LLMPlanner(self._llm, prompt=DefaultPlanningPrompt(capability_hint=caps))
            if self._llm is not None
            else TemplatePlanner()
        )
        plan = PlanningService(planner, TaskQueue()).plan(Goal(description=goal, max_steps=max_steps))

        context = KernelContext.in_memory(
            reflector=(LLMReflector(self._llm) if (self._llm and max_replans) else None),
            goal=goal,
            allowed_capabilities=caps,
            max_replans=max_replans,
        )
        kernel = Kernel(context).boot()
        for worker in workers:
            kernel.register_agent(worker)
        for step in plan.ordered_steps():
            kernel.submit(Task(description=step.description,
                               required_capabilities=step.capabilities))

        run = Run(run_id=uuid4(), goal=goal, kernel=kernel,
                  plan=[s.description for s in plan.ordered_steps()])
        run.thread = threading.Thread(target=self._execute, args=(run,),
                                      name=f"run-{run.run_id}", daemon=True)

        with self._lock:
            self._runs[run.run_id] = run
        run.thread.start()

        return CreatedRun(run_id=run.run_id, goal=goal, plan=run.plan)

    def _execute(self, run: Run) -> None:
        try:
            run.kernel.run_until_idle()
            run.status = "completed"
        except Exception as exc:  # noqa: BLE001 — isolate a run failure
            run.status = "failed"
            run.error = str(exc)
            logger.exception("Run %s failed.", run.run_id)

    # ------------------------------------------------------------------ #
    #  Lookup / projections
    # ------------------------------------------------------------------ #

    def _get(self, run_id: UUID) -> Optional[Run]:
        with self._lock:
            return self._runs.get(run_id)

    def list_runs(self) -> List[RunSummary]:
        with self._lock:
            runs = list(self._runs.values())
        return [self._summary(r) for r in runs]

    def summary(self, run_id: UUID) -> Optional[RunSummary]:
        run = self._get(run_id)
        return self._summary(run) if run else None

    def tasks(self, run_id: UUID) -> Optional[List[TaskView]]:
        run = self._get(run_id)
        if run is None:
            return None
        return [
            TaskView(
                task_id=n.task_id, description=n.description, state=n.state.value,
                capabilities=list(n.required_capabilities), depends_on=list(n.dependencies),
                assigned_worker=n.assigned_worker,
                origin=str(n.metadata.get("origin", "planned")),
            )
            for n in run.kernel.graph.nodes()
        ]

    def traces(self, run_id: UUID) -> Optional[List[TraceView]]:
        run = self._get(run_id)
        if run is None:
            return None
        views: List[TraceView] = []
        for rec in run.kernel.store.all():
            views.append(TraceView(
                task_id=rec.task_id, execution_id=rec.execution_id, worker_id=rec.agent_id,
                success=rec.success, duration_seconds=rec.duration_seconds,
                output=None if rec.output is None else str(rec.output)[:2000],
                error=rec.error,
            ))
        return views

    def events(self, run_id: UUID, *, since: int = 0) -> Optional[List[dict]]:
        """Return event history (newest last) from index *since*, JSON-safe."""
        run = self._get(run_id)
        if run is None:
            return None
        history = run.kernel.bus.history(n=1000)
        return [
            {"index": i, "type": e.type.value, "source": e.source, "payload": e.payload,
             "timestamp": e.timestamp.isoformat()}
            for i, e in enumerate(history)
            if i >= since
        ]

    def is_active(self, run_id: UUID) -> bool:
        run = self._get(run_id)
        return bool(run and run.status == "running")

    def shutdown(self) -> None:
        with self._lock:
            runs = list(self._runs.values())
        for run in runs:
            try:
                run.kernel.shutdown()
            except Exception:  # noqa: BLE001
                logger.exception("Error shutting down run %s.", run.run_id)

    # ------------------------------------------------------------------ #
    #  Internals
    # ------------------------------------------------------------------ #

    def _build_workers(self) -> list:
        return [
            ResearchAgent(llm=self._llm),
            CodingAgent(llm=self._llm),
            TestingAgent(),
            DocumentationAgent(),
        ]

    def _summary(self, run: Run) -> RunSummary:
        graph = run.kernel.graph
        return RunSummary(
            run_id=run.run_id, goal=run.goal, status=run.status, created_at=run.created_at,
            total_tasks=len(graph.nodes()),
            completed_tasks=len(graph.completed_tasks()),
            failed_tasks=len(graph.failed_tasks()),
            replans=run.kernel.health()["replans"],
            health=run.kernel.health(),
            error=run.error,
        )
