from __future__ import annotations

from pydantic import BaseModel, Field


class KernelSettings(BaseModel):
    """
    Tunable configuration for the AgentOS :class:`~kernel.kernel.Kernel`.

    Pure data — no behaviour.  Kept as a pydantic ``BaseModel`` (matching
    :class:`~models.task.Task`) so values are validated and the object is
    trivially serialisable for config files / environment overrides later.

    The Kernel reads these at construction time.  Backends that don't yet
    honour a given knob (e.g. the in-memory event bus fixes its own history
    size) treat the value as advisory until a configurable backend replaces
    them — the field still documents the intended contract.
    """

    tick_interval_seconds: float = Field(
        default=0.05,
        ge=0,
        description="Sleep between iterations of the threaded Kernel.run() loop.",
    )
    heartbeat_interval_seconds: float = Field(
        default=30.0,
        gt=0,
        description="Target interval between worker heartbeat pings.",
    )
    event_history_size: int = Field(
        default=500,
        ge=0,
        description="Number of recent events the Event Bus retains for replay/debug.",
    )
    max_retries: int = Field(
        default=3,
        ge=0,
        description="Default retry ceiling for a failed task before it is abandoned.",
    )
    agent_offline_after_seconds: float = Field(
        default=90.0,
        gt=0,
        description="Missed-heartbeat threshold after which an agent is marked OFFLINE.",
    )

    model_config = {"frozen": True}
