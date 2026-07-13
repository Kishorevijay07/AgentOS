"""
task_graph — the AgentOS Task Graph Engine.

Turns a planner :class:`~planning.models.Plan` into an executable **Directed
Acyclic Graph** of tasks. It is the single authority on *readiness*: the
Scheduler only ever asks :meth:`AbstractTaskGraph.ready_tasks`, and dependency
reasoning — cycle detection, unlocking dependents on completion — lives entirely
inside the graph.

Quick start
-----------
>>> from planning import TemplatePlanner
>>> from task_graph import PlanGraphBuilder, GraphTaskQueue
>>> plan = TemplatePlanner().plan("Build a REST API")  # from planning
>>> graph = PlanGraphBuilder().build(plan)
>>> [n.description for n in graph.ready_tasks()]        # only unblocked tasks
>>> queue = GraphTaskQueue(graph)                       # drop into the runtime
"""

from task_graph.adapter import GraphTaskQueue
from task_graph.builder import PlanGraphBuilder
from task_graph.errors import (
    CycleDetectedError,
    DuplicateTaskError,
    GraphError,
    InvalidTransitionError,
    UnknownTaskError,
)
from task_graph.graph import AbstractTaskGraph, InMemoryTaskGraph
from task_graph.node import ExecutionAttempt, TaskNode
from task_graph.observers import EventBusGraphObserver, GraphObserver
from task_graph.state import NodeState
from task_graph.visualize import GraphVisualizer, MermaidVisualizer

__all__ = [
    # graph
    "AbstractTaskGraph",
    "InMemoryTaskGraph",
    # nodes / state
    "TaskNode",
    "ExecutionAttempt",
    "NodeState",
    # build / adapt
    "PlanGraphBuilder",
    "GraphTaskQueue",
    # observers
    "GraphObserver",
    "EventBusGraphObserver",
    # visualize
    "GraphVisualizer",
    "MermaidVisualizer",
    # errors
    "GraphError",
    "DuplicateTaskError",
    "UnknownTaskError",
    "CycleDetectedError",
    "InvalidTransitionError",
]
