from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from typing import Callable, Dict, List

from distributed.codec import JSONMessageCodec, MessageCodec
from distributed.messages import Message

logger = logging.getLogger("agentos.distributed")

#: A subscriber callback. Receives a fully-typed, decoded message.
MessageHandler = Callable[[Message], None]


class Channels:
    """Well-known transport topics (control plane + data plane)."""

    REGISTER = "control.register"
    DEREGISTER = "control.deregister"
    HEARTBEAT = "control.heartbeat"
    STATUS = "control.status"
    RESULTS = "results"

    @staticmethod
    def tasks_for(worker_id: str) -> str:
        """Per-worker task inbox topic (addressed dispatch)."""
        return f"tasks.{worker_id}"


class Transport(ABC):
    """
    Abstract message transport — publish/subscribe over named topics.

    This is the single seam between "in one process" and "across many machines".
    Nothing above it (discovery, scheduler, worker nodes) knows whether messages
    travel through a dict, Redis Pub/Sub, a Kafka topic, or a NATS subject — they
    all speak :meth:`publish` / :meth:`subscribe`. Swapping brokers is a
    construction-site change, never a business-logic change.

    Semantics required of any implementation:

    * ``publish(topic, message)`` delivers *message* to every handler subscribed
      to *topic* at delivery time;
    * delivery order within a topic is preserved per publisher;
    * handlers must be isolated — one raising must not stop others.
    """

    @abstractmethod
    def publish(self, topic: str, message: Message) -> None: ...

    @abstractmethod
    def subscribe(self, topic: str, handler: MessageHandler) -> None: ...

    @abstractmethod
    def unsubscribe(self, topic: str, handler: MessageHandler) -> None: ...

    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...


class InMemoryTransport(Transport):
    """
    Thread-safe, in-process transport backed by a topic → handlers map.

    Delivery is **synchronous** (handlers run in the publisher's thread), which
    makes distributed flows deterministic to test while exercising the exact same
    code paths a networked transport would. To prove the wire contract, every
    message is round-tripped through a :class:`MessageCodec` (encode on publish,
    decode before delivery) — so anything that works here is guaranteed to be
    serialisable and will work unchanged over Redis/Kafka.

    A single lock guards the subscriber map; handlers are copied out and invoked
    without the lock held, so a handler may (re)subscribe/publish safely.
    """

    def __init__(self, *, codec: MessageCodec | None = None) -> None:
        self._subscribers: Dict[str, List[MessageHandler]] = {}
        self._lock = threading.RLock()
        self._codec = codec or JSONMessageCodec()
        self._running = False

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False
        with self._lock:
            self._subscribers.clear()

    def subscribe(self, topic: str, handler: MessageHandler) -> None:
        with self._lock:
            self._subscribers.setdefault(topic, [])
            if handler not in self._subscribers[topic]:
                self._subscribers[topic].append(handler)

    def unsubscribe(self, topic: str, handler: MessageHandler) -> None:
        with self._lock:
            handlers = self._subscribers.get(topic, [])
            if handler in handlers:
                handlers.remove(handler)

    def publish(self, topic: str, message: Message) -> None:
        # Round-trip through the codec to guarantee wire-serialisability.
        wire = self._codec.encode(message)
        delivered = self._codec.decode(wire)

        with self._lock:
            handlers = list(self._subscribers.get(topic, []))

        for handler in handlers:
            try:
                handler(delivered)
            except Exception:  # noqa: BLE001 — isolate a bad subscriber
                logger.exception("Transport handler failed on topic %r.", topic)
