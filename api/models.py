from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class GoalRequest(BaseModel):
    """POST /goals body — a goal to plan and execute."""

    goal: str = Field(min_length=1, description="The natural-language objective.")
    max_steps: int = Field(default=8, ge=1, le=50)
    max_replans: int = Field(default=0, ge=0, description="Enable the autonomous loop if > 0.")


class TaskView(BaseModel):
    """One task node's public state."""

    task_id: UUID
    description: str
    state: str
    capabilities: List[str] = Field(default_factory=list)
    depends_on: List[UUID] = Field(default_factory=list)
    assigned_worker: Optional[str] = None
    origin: str = "planned"  # "planned" or "reflection"


class TraceView(BaseModel):
    """One execution trace (from the result store)."""

    task_id: UUID
    execution_id: UUID
    worker_id: str
    success: Optional[bool]
    duration_seconds: Optional[float]
    output: Optional[str] = None
    error: Optional[str] = None


class RunSummary(BaseModel):
    """GET /runs/{id} — a run's high-level state."""

    run_id: UUID
    goal: str
    status: str  # planning | running | completed | failed
    created_at: datetime
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    replans: int
    health: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None


class CreatedRun(BaseModel):
    """POST /goals response — the run id plus the initial plan."""

    run_id: UUID
    goal: str
    plan: List[str]
