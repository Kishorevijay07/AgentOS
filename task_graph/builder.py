from __future__ import annotations

import logging
from typing import Optional

from planning.models import Plan
from planning.task_factory import TaskFactory
from task_graph.graph import AbstractTaskGraph, InMemoryTaskGraph

logger = logging.getLogger("agentos.task_graph")


class PlanGraphBuilder:
    """
    Builds an executable :class:`AbstractTaskGraph` from a planner :class:`Plan`.

    This is the single bridge between the planning vocabulary (``Plan`` /
    ``PlanStep`` with 1-based ``depends_on`` indexes) and the execution
    vocabulary (``Task`` / DAG with UUID edges). It reuses
    :class:`~planning.task_factory.TaskFactory` — the one component that already
    knows how to turn steps into tasks and remap step-order dependencies onto
    task UUIDs — so that translation is never duplicated.

    Because ``TaskFactory`` emits tasks in topological order, adding them in
    sequence wires every dependency edge as each node is inserted, and the
    graph's own cycle-guard guarantees the result is a valid DAG.
    """

    def __init__(self, task_factory: Optional[TaskFactory] = None) -> None:
        self._task_factory = task_factory or TaskFactory()

    def build(
        self,
        plan: Plan,
        *,
        graph: Optional[AbstractTaskGraph] = None,
    ) -> AbstractTaskGraph:
        """
        Convert *plan* into a task graph.

        Parameters
        ----------
        plan:
            The validated plan produced by the Planner.
        graph:
            Optional pre-constructed graph to populate (inject one wired with
            observers/visualizer). Defaults to a fresh :class:`InMemoryTaskGraph`.

        Returns
        -------
        AbstractTaskGraph
            A validated, acyclic graph with source tasks already ``READY``.
        """
        # NB: an empty graph is falsy (it defines __len__), so use `is None`.
        if graph is None:
            graph = InMemoryTaskGraph()
        tasks = self._task_factory.build(plan)
        for task in tasks:
            graph.add_task(task)
        logger.info("Built task graph with %d node(s) from plan.", len(tasks))
        return graph
