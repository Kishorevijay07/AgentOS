from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from distributed.messages import (
    DeregisterMessage,
    HeartbeatMessage,
    Message,
    RegisterMessage,
)
from distributed.transport import Channels, Transport

logger = logging.getLogger("agentos.distributed")


class WorkerPresence(str, Enum):
    """Coordinator's view of a remote worker's availability."""

    ONLINE = "online"
    OFFLINE = "offline"


class RemoteWorkerInfo(BaseModel):
    """
    The coordinator's *record* of a remote worker — data only, never a worker
    object.

    The distributed scheduler matches tasks against these records by capability
    and addresses dispatch to ``worker_id``. Because it is pure data, a worker on
    another machine is indistinguishable here from a local one — which is exactly
    the location-transparency the design requires.
    """

    worker_id: str
    capabilities: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    address: Optional[str] = None
    presence: WorkerPresence = WorkerPresence.ONLINE
    registered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_heartbeat: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class WorkerDirectory:
    """
    Service discovery: the live registry of remote workers.

    It is fed entirely by messages — it subscribes to REGISTER / DEREGISTER /
    HEARTBEAT topics and maintains the table; it never calls a worker. This keeps
    the coordinator decoupled: workers announce themselves, and the directory is
    the eventually-consistent view the scheduler reads.

    Thread-safe: a single lock guards the table; snapshots are returned to
    callers so they can iterate without holding the lock.
    """

    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._workers: Dict[str, RemoteWorkerInfo] = {}
        self._lock = threading.RLock()

    def start(self) -> None:
        """Subscribe to the control-plane topics."""
        self._transport.subscribe(Channels.REGISTER, self._on_register)
        self._transport.subscribe(Channels.DEREGISTER, self._on_deregister)
        self._transport.subscribe(Channels.HEARTBEAT, self._on_heartbeat)

    def stop(self) -> None:
        self._transport.unsubscribe(Channels.REGISTER, self._on_register)
        self._transport.unsubscribe(Channels.DEREGISTER, self._on_deregister)
        self._transport.unsubscribe(Channels.HEARTBEAT, self._on_heartbeat)

    # ------------------------------------------------------------------ #
    #  Message handlers
    # ------------------------------------------------------------------ #

    def _on_register(self, message: Message) -> None:
        assert isinstance(message, RegisterMessage)
        with self._lock:
            self._workers[message.worker_id] = RemoteWorkerInfo(
                worker_id=message.worker_id,
                capabilities=list(message.capabilities),
                metadata=dict(message.metadata),
                address=message.address,
            )
        logger.info("Worker %s registered (caps=%s).", message.worker_id, message.capabilities)

    def _on_deregister(self, message: Message) -> None:
        assert isinstance(message, DeregisterMessage)
        with self._lock:
            self._workers.pop(message.worker_id, None)
        logger.info("Worker %s deregistered.", message.worker_id)

    def _on_heartbeat(self, message: Message) -> None:
        assert isinstance(message, HeartbeatMessage)
        with self._lock:
            info = self._workers.get(message.worker_id)
            if info is None:
                return  # heartbeat from an unknown worker — ignore until it registers
            info.last_heartbeat = message.timestamp
            info.presence = WorkerPresence.ONLINE

    # ------------------------------------------------------------------ #
    #  Queries & mutation
    # ------------------------------------------------------------------ #

    def available_workers(self) -> List[RemoteWorkerInfo]:
        """Online workers — the scheduler's capability-matching candidate set."""
        with self._lock:
            return [w for w in self._workers.values() if w.presence == WorkerPresence.ONLINE]

    def all(self) -> List[RemoteWorkerInfo]:
        with self._lock:
            return list(self._workers.values())

    def get(self, worker_id: str) -> Optional[RemoteWorkerInfo]:
        with self._lock:
            return self._workers.get(worker_id)

    def mark_offline(self, worker_id: str) -> None:
        with self._lock:
            info = self._workers.get(worker_id)
            if info:
                info.presence = WorkerPresence.OFFLINE

    def remove(self, worker_id: str) -> None:
        with self._lock:
            self._workers.pop(worker_id, None)
