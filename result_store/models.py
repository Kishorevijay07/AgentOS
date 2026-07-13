from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional
from uuid import UUID, uuid4

from result_store.log_level import LogLevel


# ---------------------------------------------------------------------------
# LogEntry
# ---------------------------------------------------------------------------

@dataclass
class LogEntry:
    """
    A single structured log line emitted during an agent execution.

    Agents (or the Supervisor) call ``ResultStore.add_log(task_id, ...)``
    to append entries.  Entries are stored in emission order.

    Attributes
    ----------
    level:
        Severity — ``DEBUG``, ``INFO``, ``WARNING``, or ``ERROR``.
    message:
        Human-readable log text.
    source:
        Component that produced the entry, e.g. ``"CodingAgent-1"`` or
        ``"Supervisor"``.  Defaults to empty string when unknown.
    timestamp:
        UTC creation time.  Set automatically; callers should not override it.
    """

    level: LogLevel
    message: str
    source: str = ""
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        ts = self.timestamp.strftime("%H:%M:%S.%f")[:-3]
        src = f" [{self.source}]" if self.source else ""
        return f"[{self.level.upper()}{src} {ts}] {self.message}"


# ---------------------------------------------------------------------------
# Artifact
# ---------------------------------------------------------------------------

@dataclass
class Artifact:
    """
    A named output produced by an agent during execution.

    ``content`` is intentionally typed as ``Any`` so agents can attach
    strings, bytes, dicts, file ``Path`` objects, or any serialisable value.
    A future milestone can add a persistence backend that writes artefacts
    to disk under ``artifacts/<task_id>/<name>``.

    Attributes
    ----------
    name:
        File-like name, e.g. ``"report.md"``, ``"output.json"``,
        ``"test_results.xml"``.
    content:
        The raw artefact data.  No size limit is enforced in this milestone.
    media_type:
        MIME type hint, e.g. ``"text/markdown"``, ``"application/json"``.
        Defaults to ``"text/plain"``.
    created_at:
        UTC creation time.  Set automatically.
    """

    name: str
    content: Any
    media_type: str = "text/plain"
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        size = len(self.content) if isinstance(self.content, (str, bytes)) else "?"
        return f"Artifact(name={self.name!r}, media_type={self.media_type!r}, size={size})"


# ---------------------------------------------------------------------------
# ExecutionRecord
# ---------------------------------------------------------------------------

@dataclass
class ExecutionRecord:
    """
    Complete, immutable-after-close record of one agent execution.

    Lifecycle
    ---------
    ``ResultStore.start_execution`` creates a record with ``ended_at=None``
    and ``success=None`` (open).  ``ResultStore.finish_execution`` closes it
    by setting ``ended_at``, ``success``, ``output``, and optionally ``error``.

    Attributes
    ----------
    task_id:
        UUID of the ``Task`` this execution belongs to.  A single task may have
        **multiple** executions over its lifetime (one per attempt/retry); they
        share this ``task_id`` but each has a distinct ``execution_id``.
    agent_id:
        Registry ID of the agent that ran the task (e.g. ``"CodingAgent-1"``).
    execution_id:
        UUID unique to *this* attempt.  This is the identity of the execution —
        the ``ResultStore`` keys records by it so a retry produces a new record
        rather than overwriting the previous attempt.
    started_at:
        UTC timestamp when execution began (set by ``start_execution``).
    ended_at:
        UTC timestamp when execution finished; ``None`` while still running.
    success:
        ``True`` / ``False`` when finished; ``None`` while running.
    output:
        Raw return value of ``agent.execute(task)``; ``None`` on failure or
        while running.
    error:
        Human-readable error description when ``success is False``; otherwise
        ``None``.
    logs:
        Ordered list of :class:`LogEntry` objects appended via
        ``ResultStore.add_log``.
    artifacts:
        Named outputs appended via ``ResultStore.add_artifact``.
    """

    task_id: UUID
    agent_id: str
    started_at: datetime
    execution_id: UUID = field(default_factory=uuid4)
    ended_at: Optional[datetime] = None
    success: Optional[bool] = None
    output: Any = None
    error: Optional[str] = None
    logs: List[LogEntry] = field(default_factory=list)
    artifacts: List[Artifact] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    #  Computed properties                                                #
    # ------------------------------------------------------------------ #

    @property
    def duration_seconds(self) -> Optional[float]:
        """
        Wall-clock execution time in seconds.

        Returns ``None`` while the execution is still open (``ended_at`` is
        not set yet).
        """
        if self.ended_at is None:
            return None
        return (self.ended_at - self.started_at).total_seconds()

    @property
    def is_open(self) -> bool:
        """``True`` while the execution has not been closed yet."""
        return self.ended_at is None

    def __repr__(self) -> str:
        status = "open" if self.is_open else ("ok" if self.success else "fail")
        dur = f"{self.duration_seconds:.3f}s" if self.duration_seconds is not None else "…"
        return (
            f"ExecutionRecord(task={self.task_id}, agent={self.agent_id!r}, "
            f"status={status}, duration={dur}, "
            f"logs={len(self.logs)}, artifacts={len(self.artifacts)})"
        )
