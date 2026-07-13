"""
result_store — Module 6: Traceable execution records for AgentOS.

Every agent execution is stored as an :class:`ExecutionRecord` keyed by
``TaskID``.  Records contain structured logs, artifacts, and timing metadata.

Quick start
-----------
>>> from result_store import ResultStore, LogLevel
>>> store = ResultStore()
>>> record = store.start_execution(task.id, agent_id="CodingAgent-1")
>>> store.add_log(task.id, LogLevel.INFO, "Starting…", source="CodingAgent-1")
>>> store.finish_execution(task.id, output="done", success=True)
>>> store.get(task.id).duration_seconds
0.001
"""

from result_store.log_level import LogLevel
from result_store.models import Artifact, ExecutionRecord, LogEntry
from result_store.store import AbstractResultStore, ResultStore

__all__ = [
    "AbstractResultStore",
    "ResultStore",
    "ExecutionRecord",
    "LogEntry",
    "Artifact",
    "LogLevel",
]
