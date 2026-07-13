from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any, Callable, Optional, Protocol, runtime_checkable

logger = logging.getLogger("agentos.runtime")


@runtime_checkable
class TaskExecutor(Protocol):
    """
    Strategy for *how* a unit of work is run under a wall-clock timeout.

    Separating "run this with a deadline" from the runtime lets the isolation
    model change without touching worker management:

    * :class:`ThreadPoolTaskExecutor` — in-process threads (cooperative timeout);
    * a future ``ProcessPoolTaskExecutor`` — true isolation & hard kill;
    * a future ``RemoteTaskExecutor`` — dispatch to a worker fleet over RPC.
    """

    def run(self, fn: Callable[[], Any], *, timeout: Optional[float] = None) -> Any:
        """Invoke *fn* and return its result, raising ``TimeoutError`` past *timeout*."""
        ...

    def shutdown(self, *, wait: bool = True) -> None:
        """Release executor resources."""
        ...


class ThreadPoolTaskExecutor:
    """
    Runs work on a bounded :class:`ThreadPoolExecutor` with a per-call timeout.

    Honest caveat (documented, not hidden)
    --------------------------------------
    Python threads cannot be force-killed. On timeout the caller is unblocked and
    the task is *reported* as timed out, but the underlying thread keeps running
    until it yields. So this is a **cooperative** timeout — it protects the
    scheduler's liveness, not the worker's resources. For hard isolation and true
    cancellation, swap in a process- or container-based executor (the whole point
    of keeping this behind :class:`TaskExecutor`). Size the pool ≥ the worker
    count so a hung task cannot starve healthy ones.
    """

    def __init__(self, *, max_workers: int = 32, default_timeout: Optional[float] = None) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="agentos-worker")
        self._default_timeout = default_timeout

    def run(self, fn: Callable[[], Any], *, timeout: Optional[float] = None) -> Any:
        effective = timeout if timeout is not None else self._default_timeout
        future = self._pool.submit(fn)
        try:
            return future.result(timeout=effective)
        except FuturesTimeoutError as exc:
            # Surface a builtin TimeoutError; the runtime maps it to a domain error.
            raise TimeoutError(f"Execution exceeded {effective}s.") from exc

    def shutdown(self, *, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait)
