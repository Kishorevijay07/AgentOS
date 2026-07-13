from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from models.enums import Priority, Status


class Task(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    description: str
    priority: Priority = Priority.MEDIUM
    status: Status = Status.PENDING
    required_capabilities: List[str] = Field(default_factory=list)
    assigned_agent: Optional[str] = None
    dependencies: List[UUID] = Field(default_factory=list)
    result: Optional[str] = None

    # --- Retry & Scheduling ---
    retry_count: int = Field(default=0, ge=0, description="Number of times this task has been retried after failure.")
    deadline: Optional[datetime] = Field(default=None, description="UTC deadline; task is overdue after this timestamp.")

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"use_enum_values": True}
