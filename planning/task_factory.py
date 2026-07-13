from __future__ import annotations

from typing import Dict, List
from uuid import UUID

from models.task import Task
from planning.models import Plan


class TaskFactory:
    """
    Translates a :class:`~planning.models.Plan` into runtime :class:`Task` objects.

    This is the **only** class that knows the shape of ``models.Task``. Keeping
    the plan-vocabulary → runtime-vocabulary translation in one place means the
    planner and service stay decoupled from the execution model, and a change to
    ``Task`` ripples through exactly one file.

    Its subtle job is **dependency remapping**: a :class:`PlanStep` expresses
    dependencies against other steps' 1-based ``order`` values, but a ``Task``
    expresses them as the UUIDs of other tasks. The factory builds the tasks in
    plan order, recording an ``order → task.id`` map as it goes, so each task's
    ``dependencies`` list can be resolved to concrete UUIDs.
    """

    def build(self, plan: Plan) -> List[Task]:
        """
        Build tasks for every step in *plan*, with dependencies wired to UUIDs.

        Returns
        -------
        List[Task]
            Tasks in plan order. Task ``i``'s ``dependencies`` contain the UUIDs
            of the tasks its step's ``depends_on`` referenced.
        """
        order_to_id: Dict[int, UUID] = {}
        tasks: List[Task] = []

        for step in plan.ordered_steps():
            dependency_ids = [
                order_to_id[dep] for dep in step.depends_on if dep in order_to_id
            ]
            task = Task(
                description=step.description,
                priority=step.priority,
                required_capabilities=list(step.capabilities),
                dependencies=dependency_ids,
            )
            order_to_id[step.order] = task.id
            tasks.append(task)

        return tasks
