from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from result_store.log_level import LogLevel
from result_store.models import Artifact, ExecutionRecord, LogEntry


class AbstractResultStore(ABC):
    """
    Contract that every Result Store backend must satisfy.

    The Supervisor and Kernel depend on *this* type, never on
    :class:`ResultStore` directly.  A future ``SQLResultStore`` or
    ``RedisResultStore`` (for durable, queryable, cross-process traces) can
    slot in at the construction site without changing any consumer.
    """

    @abstractmethod
    def start_execution(self, task_id: UUID, agent_id: str) -> ExecutionRecord:
        """Open a new execution record for *task_id* before ``agent.execute``."""

    @abstractmethod
    def finish_execution(
        self,
        task_id: UUID,
        *,
        output: Any,
        success: bool,
        error: Optional[str] = None,
    ) -> ExecutionRecord:
        """Close the open execution record for *task_id*."""

    @abstractmethod
    def add_log(
        self,
        task_id: UUID,
        level: LogLevel,
        message: str,
        *,
        source: str = "",
    ) -> LogEntry:
        """Append a structured log line to the record for *task_id*."""

    @abstractmethod
    def add_artifact(
        self,
        task_id: UUID,
        name: str,
        content: Any,
        *,
        media_type: str = "text/plain",
    ) -> Artifact:
        """Attach a named artifact to the record for *task_id*."""

    @abstractmethod
    def get(self, task_id: UUID) -> Optional[ExecutionRecord]:
        """Return the **latest** execution record for *task_id*, or ``None``."""

    @abstractmethod
    def get_by_execution(self, execution_id: UUID) -> Optional[ExecutionRecord]:
        """Return the execution record with this *execution_id*, or ``None``."""

    @abstractmethod
    def executions_for(self, task_id: UUID) -> List[ExecutionRecord]:
        """Return every execution for *task_id*, oldest attempt first."""

    @abstractmethod
    def all(self) -> List[ExecutionRecord]:
        """Return a snapshot of all execution records."""

    @abstractmethod
    def query(
        self,
        *,
        agent_id: Optional[str] = None,
        since: Optional[datetime] = None,
        success: Optional[bool] = None,
    ) -> List[ExecutionRecord]:
        """Flexible filter over all records (all params ANDed together)."""


class ResultStore(AbstractResultStore):
    """
    Thread-safe, in-memory store that maps each ``TaskID`` to a rich
    :class:`ExecutionRecord`.

    Role in AgentOS
    ---------------
    The ``ResultStore`` is the **canonical trace** of every agent execution.
    It is the answer to the question: *"What happened when task X ran?"*

    - **``task.result``** (``Optional[str]``) remains for backward
      compatibility as a one-line summary.
    - **``ResultStore``** is the full, queryable record with timing,
      structured logs, and artifacts.

    Usage
    -----
    Typical call-site (Supervisor or bootstrap code)::

        store = ResultStore()

        record = store.start_execution(task.id, agent_id="CodingAgent-1")
        store.add_log(task.id, LogLevel.INFO, "Starting code generation…", source="CodingAgent-1")
        try:
            output = agent.execute(task)
            store.finish_execution(task.id, output=output, success=True)
        except Exception as exc:
            store.add_log(task.id, LogLevel.ERROR, str(exc), source="CodingAgent-1")
            store.finish_execution(task.id, output=None, success=False, error=str(exc))

        record = store.get(task.id)
        print(record.duration_seconds, record.logs)

    Thread safety
    -------------
    A single ``threading.Lock`` guards all mutations.  The lock is released
    before returning values so callers are never blocked longer than necessary.

    Swappability
    ------------
    ``ResultStore`` implements ``AbstractResultStore`` (defined in this module).
    A future ``RedisResultStore`` or ``SQLResultStore`` can slot in at the
    construction site without changing any publisher/subscriber code.
    """

    def __init__(self) -> None:
        # Executions are keyed by their own execution_id, so a task that runs
        # more than once (retry) produces distinct records instead of
        # overwriting.  Two secondary indexes keep the task_id-facing API cheap:
        self._records: Dict[UUID, ExecutionRecord] = {}      # execution_id -> record
        self._by_task: Dict[UUID, List[UUID]] = {}           # task_id -> [execution_id, ...]
        self._open_by_task: Dict[UUID, UUID] = {}            # task_id -> open execution_id
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    #  Lifecycle — open / close an execution                             #
    # ------------------------------------------------------------------ #

    def start_execution(self, task_id: UUID, agent_id: str) -> ExecutionRecord:
        """
        Open a new :class:`ExecutionRecord` for *task_id*.

        Must be called **before** ``agent.execute(task)``.  Records the UTC
        start time and marks the execution as open (``ended_at=None``).

        A task may be executed more than once (retry): each call opens a **new**
        record with its own ``execution_id``.  What is *not* allowed is two
        simultaneously-open executions for the same task.

        Raises
        ------
        ValueError
            If an open record already exists for *task_id*.  Call
            :meth:`finish_execution` first.

        Returns
        -------
        ExecutionRecord
            The freshly created (open) record.
        """
        with self._lock:
            if task_id in self._open_by_task:
                raise ValueError(
                    f"An open execution record already exists for task {task_id}. "
                    "Call finish_execution() before starting a new one."
                )
            record = ExecutionRecord(
                task_id=task_id,
                agent_id=agent_id,
                started_at=datetime.now(timezone.utc),
            )
            self._records[record.execution_id] = record
            self._by_task.setdefault(task_id, []).append(record.execution_id)
            self._open_by_task[task_id] = record.execution_id
            return record

    def finish_execution(
        self,
        task_id: UUID,
        *,
        output: Any,
        success: bool,
        error: Optional[str] = None,
    ) -> ExecutionRecord:
        """
        Close the open execution record for *task_id*.

        Sets ``ended_at``, ``success``, ``output``, and ``error`` on the
        record.  After this call :attr:`ExecutionRecord.duration_seconds`
        becomes available.

        Parameters
        ----------
        task_id:
            Must match a record previously opened by :meth:`start_execution`.
        output:
            Raw return value of ``agent.execute(task)``; pass ``None`` on
            failure.
        success:
            ``True`` if the agent completed without raising.
        error:
            Human-readable error description; use when ``success=False``.

        Raises
        ------
        KeyError
            If no record exists for *task_id*.
        RuntimeError
            If the record for *task_id* is already closed.

        Returns
        -------
        ExecutionRecord
            The now-closed record.
        """
        with self._lock:
            record = self._get_open_record(task_id)
            record.ended_at = datetime.now(timezone.utc)
            record.success = success
            record.output = output
            record.error = error
            # No longer the open execution for this task.
            self._open_by_task.pop(task_id, None)
            return record

    # ------------------------------------------------------------------ #
    #  Logging                                                            #
    # ------------------------------------------------------------------ #

    def add_log(
        self,
        task_id: UUID,
        level: LogLevel,
        message: str,
        *,
        source: str = "",
    ) -> LogEntry:
        """
        Append a structured log line to the execution record for *task_id*.

        Can be called at any point between :meth:`start_execution` and
        :meth:`finish_execution` (or even after — the record just has to
        exist).

        Parameters
        ----------
        task_id:
            Target execution record.
        level:
            Log severity (:class:`LogLevel`).
        message:
            Human-readable log text.
        source:
            Component identifier, e.g. ``"CodingAgent-1"`` or ``"Supervisor"``.

        Returns
        -------
        LogEntry
            The entry that was appended.
        """
        entry = LogEntry(level=level, message=message, source=source)
        with self._lock:
            record = self._get_record(task_id)
            record.logs.append(entry)
        return entry

    # ------------------------------------------------------------------ #
    #  Artifacts                                                          #
    # ------------------------------------------------------------------ #

    def add_artifact(
        self,
        task_id: UUID,
        name: str,
        content: Any,
        *,
        media_type: str = "text/plain",
    ) -> Artifact:
        """
        Attach a named artifact to the execution record for *task_id*.

        Parameters
        ----------
        task_id:
            Target execution record.
        name:
            File-like name, e.g. ``"report.md"``, ``"output.json"``.
        content:
            Raw artifact data — str, bytes, dict, ``pathlib.Path``, etc.
        media_type:
            MIME type hint.  Defaults to ``"text/plain"``.

        Returns
        -------
        Artifact
            The artifact that was appended.
        """
        artifact = Artifact(name=name, content=content, media_type=media_type)
        with self._lock:
            record = self._get_record(task_id)
            record.artifacts.append(artifact)
        return artifact

    # ------------------------------------------------------------------ #
    #  Querying                                                           #
    # ------------------------------------------------------------------ #

    def get(self, task_id: UUID) -> Optional[ExecutionRecord]:
        """
        Return the **latest** :class:`ExecutionRecord` for *task_id*, or ``None``.

        For a task that ran once this is simply that record; for a retried task
        it is the most recent attempt.  Use :meth:`executions_for` to see every
        attempt.
        """
        with self._lock:
            ids = self._by_task.get(task_id)
            return self._records[ids[-1]] if ids else None

    def get_by_execution(self, execution_id: UUID) -> Optional[ExecutionRecord]:
        """Return the record with this *execution_id*, or ``None``."""
        with self._lock:
            return self._records.get(execution_id)

    def executions_for(self, task_id: UUID) -> List[ExecutionRecord]:
        """Return every execution for *task_id*, oldest attempt first."""
        with self._lock:
            return [self._records[i] for i in self._by_task.get(task_id, [])]

    def all(self) -> List[ExecutionRecord]:
        """Return a snapshot of **all** execution records."""
        with self._lock:
            return list(self._records.values())

    def successful(self) -> List[ExecutionRecord]:
        """Return only records where ``success is True``."""
        with self._lock:
            return [r for r in self._records.values() if r.success is True]

    def failed(self) -> List[ExecutionRecord]:
        """Return only records where ``success is False``."""
        with self._lock:
            return [r for r in self._records.values() if r.success is False]

    def open_executions(self) -> List[ExecutionRecord]:
        """Return records that have been started but not yet finished."""
        with self._lock:
            return [r for r in self._records.values() if r.is_open]

    def query(
        self,
        *,
        agent_id: Optional[str] = None,
        since: Optional[datetime] = None,
        success: Optional[bool] = None,
    ) -> List[ExecutionRecord]:
        """
        Flexible filter over all records.

        All provided parameters are ANDed together.

        Parameters
        ----------
        agent_id:
            If given, only records produced by this agent are returned.
        since:
            If given, only records whose ``started_at`` is ≥ *since*.
        success:
            If given, filter by success flag.  ``None`` (default) → no filter.

        Returns
        -------
        List[ExecutionRecord]
            Matching records, ordered by ``started_at`` ascending.
        """
        with self._lock:
            results = list(self._records.values())

        if agent_id is not None:
            results = [r for r in results if r.agent_id == agent_id]
        if since is not None:
            results = [r for r in results if r.started_at >= since]
        if success is not None:
            results = [r for r in results if r.success is success]

        return sorted(results, key=lambda r: r.started_at)

    # ------------------------------------------------------------------ #
    #  Internals                                                          #
    # ------------------------------------------------------------------ #

    def _get_record(self, task_id: UUID) -> ExecutionRecord:
        """
        Retrieve the relevant record for *task_id* (open one if any, else the
        latest attempt).  Must be called under lock.
        """
        exec_id = self._open_by_task.get(task_id)
        if exec_id is None:
            ids = self._by_task.get(task_id)
            if not ids:
                raise KeyError(f"No execution record found for task {task_id!r}.")
            exec_id = ids[-1]  # latest closed attempt
        return self._records[exec_id]

    def _get_open_record(self, task_id: UUID) -> ExecutionRecord:
        """Retrieve the currently-open record for *task_id*. Must be called under lock."""
        exec_id = self._open_by_task.get(task_id)
        if exec_id is None:
            if task_id in self._by_task:
                raise RuntimeError(
                    f"Execution record for task {task_id!r} is already closed."
                )
            raise KeyError(f"No execution record found for task {task_id!r}.")
        return self._records[exec_id]

    def __len__(self) -> int:
        """Total number of records (open + closed)."""
        with self._lock:
            return len(self._records)

    def __repr__(self) -> str:
        with self._lock:
            total = len(self._records)
            open_ = sum(1 for r in self._records.values() if r.is_open)
            ok = sum(1 for r in self._records.values() if r.success is True)
            fail = sum(1 for r in self._records.values() if r.success is False)
        return (
            f"ResultStore(total={total}, open={open_}, "
            f"successful={ok}, failed={fail})"
        )
