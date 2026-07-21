"""
distributed — the AgentOS Distributed Runtime Layer.

Evolves AgentOS from a single process into a runtime that coordinates workers
across machines, communicating **only** through messages over a pluggable
transport. The scheduler never learns whether a worker is local or remote.

Subsystems
----------
* **messages / codec** — the strongly-typed wire protocol (Register, Heartbeat,
  Task, Result, Error, Status, Deregister) and its JSON codec.
* **transport** — the ``Transport`` abstraction + ``InMemoryTransport`` today;
  Redis/Kafka/NATS tomorrow, with no business-logic change.
* **discovery** — ``WorkerDirectory``: message-fed service discovery.
* **heartbeat** — ``HeartbeatEmitter`` (worker side) + ``HeartbeatMonitor``
  (coordinator side) for liveness and cleanup.
* **remote_worker** — ``RemoteWorkerNode``: a worker daemon that runs tasks
  received over the transport (reusing the Sprint-6 runtime).
* **scheduler** — ``DistributedScheduler``: capability-based dispatch over the
  transport, correlating async results back into the Task Graph.
"""

from distributed.codec import JSONMessageCodec, MessageCodec
from distributed.discovery import RemoteWorkerInfo, WorkerDirectory, WorkerPresence
from distributed.heartbeat import HeartbeatEmitter, HeartbeatMonitor
from distributed.messages import (
    DeregisterMessage,
    ErrorMessage,
    HeartbeatMessage,
    Message,
    MessageType,
    RegisterMessage,
    ResultMessage,
    StatusMessage,
    TaskMessage,
)
from distributed.redis_transport import RedisTransport
from distributed.remote_worker import RemoteWorkerNode
from distributed.scheduler import DistributedScheduler
from distributed.transport import Channels, InMemoryTransport, Transport

__all__ = [
    # protocol
    "Message",
    "MessageType",
    "RegisterMessage",
    "DeregisterMessage",
    "HeartbeatMessage",
    "TaskMessage",
    "ResultMessage",
    "ErrorMessage",
    "StatusMessage",
    "MessageCodec",
    "JSONMessageCodec",
    # transport
    "Transport",
    "InMemoryTransport",
    "RedisTransport",
    "Channels",
    # discovery
    "WorkerDirectory",
    "RemoteWorkerInfo",
    "WorkerPresence",
    # heartbeat
    "HeartbeatEmitter",
    "HeartbeatMonitor",
    # nodes + scheduler
    "RemoteWorkerNode",
    "DistributedScheduler",
]
