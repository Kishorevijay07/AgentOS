from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Type
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from models.task import Task


class MessageType(str, Enum):
    """Discriminator for every message that crosses the transport."""

    REGISTER = "register"
    DEREGISTER = "deregister"
    HEARTBEAT = "heartbeat"
    TASK = "task"
    RESULT = "result"
    ERROR = "error"
    STATUS = "status"


class Message(BaseModel):
    """
    Base envelope shared by every wire message.

    Every message is a Pydantic model, so the entire protocol is strongly typed
    *and* JSON-serialisable — which is exactly what lets the in-memory transport
    be swapped for Redis/Kafka/NATS without changing any business logic (the
    payloads already travel as validated JSON).
    """

    type: MessageType
    message_id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sender: str = Field(description="Node/worker id that produced this message.")


class RegisterMessage(Message):
    """Worker → coordinator: 'I am online and can do X'."""

    type: MessageType = MessageType.REGISTER
    worker_id: str
    capabilities: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    address: Optional[str] = None  # host:port / node id for future remote transports


class DeregisterMessage(Message):
    """Worker → coordinator: graceful departure."""

    type: MessageType = MessageType.DEREGISTER
    worker_id: str


class HeartbeatMessage(Message):
    """Worker → coordinator: periodic liveness ping."""

    type: MessageType = MessageType.HEARTBEAT
    worker_id: str
    state: str = "idle"
    metrics: Dict[str, Any] = Field(default_factory=dict)


class TaskMessage(Message):
    """Coordinator → worker: 'run this task'."""

    type: MessageType = MessageType.TASK
    worker_id: str  # intended recipient
    task: Task
    execution_id: Optional[UUID] = None
    timeout: Optional[float] = None


class ResultMessage(Message):
    """Worker → coordinator: structured execution result."""

    type: MessageType = MessageType.RESULT
    worker_id: str
    task_id: UUID
    success: bool
    output: Any = None
    error: Optional[str] = None
    duration_seconds: float = 0.0
    timed_out: bool = False
    execution_id: Optional[UUID] = None


class ErrorMessage(Message):
    """Any node → coordinator: out-of-band error report."""

    type: MessageType = MessageType.ERROR
    worker_id: Optional[str] = None
    error: str
    context: Dict[str, Any] = Field(default_factory=dict)


class StatusMessage(Message):
    """Worker → coordinator: on-demand status / metrics snapshot."""

    type: MessageType = MessageType.STATUS
    worker_id: str
    state: str
    metrics: Dict[str, Any] = Field(default_factory=dict)


#: Registry used by the codec to reconstruct the right subclass from the wire.
MESSAGE_REGISTRY: Dict[MessageType, Type[Message]] = {
    MessageType.REGISTER: RegisterMessage,
    MessageType.DEREGISTER: DeregisterMessage,
    MessageType.HEARTBEAT: HeartbeatMessage,
    MessageType.TASK: TaskMessage,
    MessageType.RESULT: ResultMessage,
    MessageType.ERROR: ErrorMessage,
    MessageType.STATUS: StatusMessage,
}
