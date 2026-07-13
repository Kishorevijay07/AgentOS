from __future__ import annotations

from enum import Enum


class EventType(str, Enum):
    """
    Canonical set of events that flow through the AgentOS Event Bus.

    Using ``str`` as a mixin makes each value directly JSON-serialisable
    and printable without extra conversion.

    Task lifecycle
    --------------
    TASK_CREATED   — a new Task was pushed onto the TaskQueue.
    TASK_ASSIGNED  — the Scheduler matched a task to an agent.
    TASK_STARTED   — a worker called ``execute()`` on the task.
    TASK_COMPLETED — the worker finished successfully.
    TASK_FAILED    — the worker raised an exception or returned a failure.

    Agent lifecycle
    ---------------
    AGENT_ONLINE     — an agent was registered and initialised.
    AGENT_OFFLINE    — an agent was shut down or marked offline.
    AGENT_HEARTBEAT  — periodic keep-alive ping from a running worker.
    """

    # --- Task lifecycle ---
    TASK_CREATED = "task.created"
    TASK_READY = "task.ready"          # dependencies satisfied; eligible to run
    TASK_ASSIGNED = "task.assigned"
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"

    # --- Agent lifecycle ---
    AGENT_ONLINE = "agent.online"
    AGENT_OFFLINE = "agent.offline"
    AGENT_HEARTBEAT = "agent.heartbeat"
