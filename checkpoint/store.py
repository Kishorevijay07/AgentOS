from __future__ import annotations

import logging
import os
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from checkpoint.models import Checkpoint

logger = logging.getLogger("agentos.checkpoint")


class CheckpointStore(ABC):
    """
    Port for durably persisting and retrieving a :class:`Checkpoint`.

    The Kernel depends on this interface, never on a concrete store — so a
    file-backed store today can become a Redis/S3/database store tomorrow with
    no Kernel change (ADR-0008). ``load`` returns ``None`` when no checkpoint
    exists yet, so callers can start-or-resume uniformly.
    """

    @abstractmethod
    def save(self, checkpoint: Checkpoint) -> None:
        """Persist *checkpoint*, replacing any previous one."""

    @abstractmethod
    def load(self) -> Optional[Checkpoint]:
        """Return the latest checkpoint, or ``None`` if none is stored."""


class InMemoryCheckpointStore(CheckpointStore):
    """Process-local store — for tests and single-process resume within a run."""

    def __init__(self) -> None:
        self._checkpoint: Optional[Checkpoint] = None
        self._lock = threading.Lock()

    def save(self, checkpoint: Checkpoint) -> None:
        with self._lock:
            # Round-trip through JSON so the stored copy is a true snapshot,
            # decoupled from later mutation of the live nodes.
            self._checkpoint = Checkpoint.model_validate_json(checkpoint.model_dump_json())

    def load(self) -> Optional[Checkpoint]:
        with self._lock:
            return self._checkpoint


class FileCheckpointStore(CheckpointStore):
    """
    JSON-file checkpoint store with an **atomic** write.

    ``save`` writes to a temp file and ``os.replace``-s it into place, so a crash
    mid-write can never corrupt the checkpoint — the file is always either the
    old snapshot or the new one, never a half-written mix. That atomicity is the
    whole point of a checkpoint you can trust after a crash.
    """

    def __init__(self, path: str | os.PathLike) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    def save(self, checkpoint: Checkpoint) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(checkpoint.model_dump_json(indent=2), encoding="utf-8")
            os.replace(tmp, self._path)  # atomic on POSIX and Windows
        logger.info("Checkpoint saved to %s (%s).", self._path, checkpoint.summary())

    def load(self) -> Optional[Checkpoint]:
        with self._lock:
            if not self._path.exists():
                return None
            return Checkpoint.model_validate_json(self._path.read_text(encoding="utf-8"))
