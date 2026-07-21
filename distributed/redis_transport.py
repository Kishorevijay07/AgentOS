from __future__ import annotations

import logging
import threading
from typing import Dict, List, Optional

from distributed.codec import JSONMessageCodec, MessageCodec
from distributed.messages import Message
from distributed.transport import MessageHandler, Transport

logger = logging.getLogger("agentos.distributed")


class RedisTransport(Transport):
    """
    :class:`Transport` backed by Redis Pub/Sub — the broker-swap proof.

    Everything above the transport (worker directory, heartbeats, remote worker
    nodes, the unified scheduler's transport backend) is untouched when this
    replaces :class:`InMemoryTransport`: messages already travel as codec-encoded
    JSON, and every collaborator depends on the ``Transport`` ABC (ADR-0008).
    Construction is the only thing that changes::

        transport = RedisTransport("redis://localhost:6379/0")

    Mechanics
    ---------
    * ``publish`` → ``PUBLISH <topic> <codec-encoded message>``.
    * ``subscribe`` registers the handler locally and subscribes the underlying
      Redis pubsub to the topic; a single background listener thread decodes
      each incoming payload and fans it out to that topic's handlers.
    * Handler isolation matches the in-memory transport: one raising handler
      cannot stop the others.

    Notes for production
    --------------------
    Redis Pub/Sub is **fire-and-forget**: messages published while a node is
    disconnected are lost (at-most-once). That matches the in-memory semantics,
    so behaviour is consistent — but durable delivery needs Redis Streams or
    Kafka behind this same ABC.

    Testing seam: pass ``client`` to inject a fake Redis (no server needed).
    The ``redis`` package is imported lazily so AgentOS itself never requires it.
    """

    def __init__(
        self,
        url: str = "redis://localhost:6379/0",
        *,
        client=None,
        codec: Optional[MessageCodec] = None,
    ) -> None:
        if client is None:
            import redis  # lazy — only needed when actually using this backend

            client = redis.Redis.from_url(url, decode_responses=True)
        self._client = client
        self._codec = codec or JSONMessageCodec()

        self._handlers: Dict[str, List[MessageHandler]] = {}
        self._lock = threading.RLock()
        self._pubsub = None
        self._listener: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Open the pubsub connection and start the listener thread."""
        if self._listener is not None and self._listener.is_alive():
            return
        self._stop.clear()
        self._pubsub = self._client.pubsub(ignore_subscribe_messages=True)
        # Re-attach any topics subscribed before start().
        with self._lock:
            topics = list(self._handlers.keys())
        if topics:
            self._pubsub.subscribe(*topics)
        self._listener = threading.Thread(
            target=self._listen, name="agentos-redis-transport", daemon=True
        )
        self._listener.start()
        logger.info("RedisTransport started (topics=%s).", topics)

    def stop(self) -> None:
        self._stop.set()
        if self._pubsub is not None:
            try:
                self._pubsub.close()
            except Exception:  # noqa: BLE001
                pass
        if self._listener is not None:
            self._listener.join(timeout=2.0)
        self._listener = None
        with self._lock:
            self._handlers.clear()

    # ------------------------------------------------------------------ #
    #  Pub/Sub
    # ------------------------------------------------------------------ #

    def publish(self, topic: str, message: Message) -> None:
        self._client.publish(topic, self._codec.encode(message))

    def subscribe(self, topic: str, handler: MessageHandler) -> None:
        with self._lock:
            bucket = self._handlers.setdefault(topic, [])
            if handler not in bucket:
                bucket.append(handler)
            fresh_topic = len(bucket) == 1
        if fresh_topic and self._pubsub is not None:
            self._pubsub.subscribe(topic)

    def unsubscribe(self, topic: str, handler: MessageHandler) -> None:
        with self._lock:
            bucket = self._handlers.get(topic, [])
            if handler in bucket:
                bucket.remove(handler)
            topic_empty = not bucket
        if topic_empty and self._pubsub is not None:
            try:
                self._pubsub.unsubscribe(topic)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ #
    #  Listener
    # ------------------------------------------------------------------ #

    def _listen(self) -> None:
        while not self._stop.is_set():
            try:
                raw = self._pubsub.get_message(timeout=0.2)
            except Exception:  # noqa: BLE001 — connection closed during stop()
                if self._stop.is_set():
                    return
                logger.exception("RedisTransport listener error; retrying.")
                continue
            if raw is None or raw.get("type") != "message":
                continue
            self._dispatch(raw["channel"], raw["data"])

    def _dispatch(self, topic: str, payload: str) -> None:
        try:
            message = self._codec.decode(payload)
        except Exception:  # noqa: BLE001 — never let a bad payload kill the loop
            logger.exception("RedisTransport dropped undecodable payload on %r.", topic)
            return
        with self._lock:
            handlers = list(self._handlers.get(topic, []))
        for handler in handlers:
            try:
                handler(message)
            except Exception:  # noqa: BLE001 — isolate a bad subscriber
                logger.exception("Transport handler failed on topic %r.", topic)
