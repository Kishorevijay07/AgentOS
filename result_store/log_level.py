from __future__ import annotations

from enum import Enum


class LogLevel(str, Enum):
    """
    Severity levels for structured log entries emitted during agent execution.

    Using ``str`` as a mixin makes values JSON-serialisable and printable
    without extra conversion — consistent with ``EventType`` and ``AgentStatus``.
    """

    DEBUG   = "debug"
    INFO    = "info"
    WARNING = "warning"
    ERROR   = "error"
