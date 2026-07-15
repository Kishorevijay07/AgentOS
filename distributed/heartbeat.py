from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Callable, List, Optional

from distributed.discovery import WorkerDirectory, WorkerPresence
from distributed.messages import HeartbeatMessage
from distributed.transport import Channels, Transport

logger = logging.getLogger("agentos.distributed")


class HeartbeatEmitter:
    """
    Worker-side heartbeat producer.

    Runs a daemon thread that publishes a :class:`HeartbeatMessage` every
    ``interval`` seconds so the coordinator's :class:`WorkerDirectory` keeps the
    worker marked online. The current state is pulled from an injected callable,
    so the emitter never reaches into the worker.
    """

    def __init__(
        self,
        transport: Transport,
        worker_id: str,
        *,
        interval: float = 5.0,
        state_provider: Optional[Callable[[], str]] = None,
    ) -> None:
        self._transport = transport
        self._worker_id = worker_id
        self._interval = interval
        self._state_provider = state_provider or (lambda: "idle")
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name=f"heartbeat-{self._worker_id}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def beat_once(self) -> None:
        """Publish a single heartbeat immediately (also used deterministically in tests)."""
        self._transport.publish(
            Channels.HEARTBEAT,
            HeartbeatMessage(
                sender=self._worker_id,
                worker_id=self._worker_id,
                state=self._state_provider(),
            ),
        )

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.beat_once()
            self._stop.wait(self._interval)


class HeartbeatMonitor:
    """
    Coordinator-side liveness monitor.

    Scans the :class:`WorkerDirectory` and marks any worker whose last heartbeat
    is older than ``timeout`` as OFFLINE; if ``evict`` is set, it also removes
    them (automatic cleanup). Can be driven manually via :meth:`check` (used in
    tests and by the Kernel tick) or run as its own daemon loop.
    """

    def __init__(
        self,
        directory: WorkerDirectory,
        *,
        timeout: float = 15.0,
        evict: bool = False,
        interval: float = 5.0,
    ) -> None:
        self._directory = directory
        self._timeout = timeout
        self._evict = evict
        self._interval = interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def check(self, *, now: Optional[datetime] = None) -> List[str]:
        """Mark stale workers offline (and optionally evict). Returns affected ids."""
        now = now or datetime.now(timezone.utc)
        affected: List[str] = []
        for info in self._directory.all():
            if info.presence != WorkerPresence.ONLINE:
                continue
            if (now - info.last_heartbeat).total_seconds() > self._timeout:
                self._directory.mark_offline(info.worker_id)
                if self._evict:
                    self._directory.remove(info.worker_id)
                affected.append(info.worker_id)
        if affected:
            logger.warning("Heartbeat monitor took workers offline: %s", affected)
        return affected

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="heartbeat-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.check()
            self._stop.wait(self._interval)
