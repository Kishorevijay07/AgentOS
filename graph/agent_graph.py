from __future__ import annotations

from typing import Any, Dict, List

from langgraph.graph import END, StateGraph

from agents.planner import PlannerAgent
from agents.reflection import ReflectionAgent
from graph.state import GraphState
from models.task import Task
from queue.result_queue import ResultQueue
from queue.task_queue import TaskQueue
from supervisor.supervisor import Supervisor


class AgentGraph:
    """
    Wires the entire agent system into a LangGraph ``StateGraph``.

    Graph topology
    --------------
    ::

        START
          │
          ▼
        planner          ← decomposes the goal into subtasks
          │
          ▼
        supervisor       ← dispatches one task per pass
          │
          ▼
        reflection       ← evaluates results; may inject retries
          │
          ▼ (conditional)
        ┌─ done? ─────────────────────► END
        │
        └─ not done? ────────────────► supervisor  (loop)

    The graph is built lazily on the first call to :py:meth:`run`.
    All mutable state (queues, agents) is injected via the constructor
    so the graph stays pure and testable.
    """

    def __init__(
        self,
        planner: PlannerAgent,
        supervisor: Supervisor,
        reflection: ReflectionAgent,
        task_queue: TaskQueue,
        result_queue: ResultQueue,
    ) -> None:
        self._planner = planner
        self._supervisor = supervisor
        self._reflection = reflection
        self._task_queue = task_queue
        self._result_queue = result_queue
        self._compiled = None

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def build(self) -> Any:
        """
        Construct and compile the ``StateGraph``.

        Returns
        -------
        CompiledGraph
            The compiled LangGraph object (also cached on ``self._compiled``).
        """
        graph: StateGraph = StateGraph(GraphState)

        graph.add_node("planner", self._planner_node)
        graph.add_node("supervisor", self._supervisor_node)
        graph.add_node("reflection", self._reflection_node)

        graph.set_entry_point("planner")
        graph.add_edge("planner", "supervisor")
        graph.add_edge("supervisor", "reflection")
        graph.add_conditional_edges(
            "reflection",
            self._should_continue,
            {"continue": "supervisor", "end": END},
        )

        self._compiled = graph.compile()
        return self._compiled

    def run(self, goal: str) -> GraphState:
        """
        Execute the full agent pipeline for the given goal.

        Parameters
        ----------
        goal:
            High-level instruction, e.g. ``"Build REST API"``.

        Returns
        -------
        GraphState
            Final state after the graph reaches END.
        """
        if self._compiled is None:
            self.build()

        initial: GraphState = {
            "goal": goal,
            "pending_count": 0,
            "results": [],
            "retry_count": 0,
            "done": False,
        }
        return self._compiled.invoke(initial)

    # ------------------------------------------------------------------ #
    #  Node implementations
    # ------------------------------------------------------------------ #

    def _planner_node(self, state: GraphState) -> Dict[str, Any]:
        """
        Decompose the goal into subtasks and enqueue them.

        Returns
        -------
        dict
            Updated ``pending_count``.
        """
        planner_task = Task(description=state["goal"])
        subtasks: List[Task] = self._planner.execute(planner_task)

        for subtask in subtasks:
            self._task_queue.add_task(subtask)

        return {"pending_count": len(subtasks)}

    def _supervisor_node(self, state: GraphState) -> Dict[str, Any]:
        """
        Dispatch one pending task to a worker.

        Returns
        -------
        dict
            Updated ``pending_count`` (tasks still waiting in the queue).
        """
        self._supervisor.run_once()
        return {"pending_count": len(self._task_queue)}

    def _reflection_node(self, state: GraphState) -> Dict[str, Any]:
        """
        Drain the ResultQueue and evaluate every result.

        Poor-quality results trigger a new corrective ``Task`` injected
        back into the ``TaskQueue`` so the Supervisor can pick it up on
        the next loop iteration.

        Returns
        -------
        dict
            Updated ``results``, ``retry_count``, and ``done`` flag.
        """
        accumulated: List[str] = list(state["results"])
        retry_count: int = state["retry_count"]

        for agent_result in self._result_queue.drain():
            accumulated.append(str(agent_result.output))
            retry_task = self._reflection.evaluate_result(agent_result)
            if retry_task is not None:
                self._task_queue.add_task(retry_task)
                retry_count += 1

        done = self._task_queue.is_empty()
        return {"results": accumulated, "retry_count": retry_count, "done": done}

    # ------------------------------------------------------------------ #
    #  Conditional edge
    # ------------------------------------------------------------------ #

    def _should_continue(self, state: GraphState) -> str:
        """
        Routing function for the conditional edge after the reflection node.

        Returns
        -------
        str
            ``"end"`` when all tasks are done, ``"continue"`` to loop back
            to the supervisor.
        """
        return "end" if state["done"] else "continue"
