from __future__ import annotations

import logging
from typing import Optional

from checkpoint.models import Checkpoint
from checkpoint.store import CheckpointStore

logger = logging.getLogger("agentos.checkpoint")


class RedisCheckpointStore(CheckpointStore):
    """
    A :class:`CheckpointStore` backed by Redis — checkpoints that outlive a
    single machine.

    This is what makes crash-resume work **across machines**: a coordinator on
    node A saves the run state to Redis, and a coordinator on node B can load it
    and continue. Because the Kernel depends only on the ``CheckpointStore`` port
    (ADR-0008), switching from a local file to Redis is a one-line construction
    change — nothing else in the runtime moves.

    The checkpoint is stored as a single JSON string under ``key`` (a `SET`), so
    a save is atomic from any reader's point of view. ``redis`` is imported
    lazily so AgentOS never hard-requires it; pass ``client`` to inject a fake in
    tests (no server needed).
    """

    def __init__(
        self,
        url: str = "redis://localhost:6379/0",
        *,
        key: str = "agentos:checkpoint",
        client=None,
    ) -> None:
        if client is None:
            import redis  # lazy — only needed when this backend is used

            client = redis.Redis.from_url(url, decode_responses=True)
        self._client = client
        self._key = key

    def save(self, checkpoint: Checkpoint) -> None:
        self._client.set(self._key, checkpoint.model_dump_json())
        logger.info("Checkpoint saved to Redis key %r (%s).", self._key, checkpoint.summary())

    def load(self) -> Optional[Checkpoint]:
        raw = self._client.get(self._key)
        if raw is None:
            return None
        if isinstance(raw, bytes):  # client without decode_responses
            raw = raw.decode("utf-8")
        return Checkpoint.model_validate_json(raw)
