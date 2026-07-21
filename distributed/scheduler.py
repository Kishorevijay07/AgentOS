from __future__ import annotations

import logging
from typing import Optional

from events.bus import AbstractEventBus
from result_store import AbstractResultStore
from scheduling.backend import TransportDispatchBackend
from scheduling.capability import CapabilityMatcher
from scheduling.retry import RetryPolicy
from scheduling.scheduler import ExecutionScheduler
from task_graph.graph import AbstractTaskGraph


class DistributedScheduler(ExecutionScheduler):
    """
    Convenience wrapper: the unified :class:`ExecutionScheduler` pre-wired with a
    :class:`~scheduling.backend.TransportDispatchBackend`.

    Since v0.7 there is **one** scheduler implementation. This class only
    preserves the ergonomic distributed constructor
    (``DistributedScheduler(graph, directory, transport)``) and the historical
    name — every behaviour (capability placement, retries, lost-worker reaping,
    graph reconciliation) lives in the parent class and is shared verbatim with
    local execution. See ADR-0011.
    """

    def __init__(
        self,
        graph: AbstractTaskGraph,
        directory,
        transport,
        *,
        matcher: Optional[CapabilityMatcher] = None,
        retry_policy: Optional[RetryPolicy] = None,
        event_bus: Optional[AbstractEventBus] = None,
        result_store: Optional[AbstractResultStore] = None,
        task_timeout: Optional[float] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        super().__init__(
            graph,
            backend=TransportDispatchBackend(directory, transport),
            matcher=matcher,
            retry_policy=retry_policy,
            event_bus=event_bus,
            result_store=result_store,
            task_timeout=task_timeout,
            logger=logger or logging.getLogger("agentos.distributed"),
        )

    # Historical alias: the distributed API called a wave "dispatch_ready".
    def dispatch_ready(self) -> int:
        """Alias for :meth:`ExecutionScheduler.schedule_wave`."""
        return self.schedule_wave()
