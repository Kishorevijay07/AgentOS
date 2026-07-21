from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from config.settings import KernelSettings
from events.bus import AbstractEventBus, InMemoryEventBus
from result_store import AbstractResultStore, ResultStore
from runtime.runtime import AbstractWorkerRuntime, DefaultWorkerRuntime
from scheduling.backend import LocalDispatchBackend
from scheduling.scheduler import ExecutionScheduler
from task_graph.graph import AbstractTaskGraph, InMemoryTaskGraph


@dataclass(frozen=True)
class KernelContext:
    """
    Shared-services container passed to every runtime module.

    Since v0.7 the Kernel runs on the **graph runtime**: work lives in an
    :class:`AbstractTaskGraph` (a DAG, not a flat queue), workers live in an
    :class:`AbstractWorkerRuntime` (lifecycle, timeouts, isolation, metrics),
    and one unified :class:`ExecutionScheduler` places ready tasks on capable
    workers through a pluggable dispatch backend (see ADR-0011). The former
    queue/registry/Dispatcher trio is retired.

    Every field is typed as its **abstraction** (ADR-0008), so the whole graph
    can be pointed at distributed backends by passing overrides to
    :meth:`in_memory` — no downstream module names a concrete type.

    Frozen: the wiring is fixed for a Kernel's lifetime. The services it holds
    are themselves mutable (the graph fills, workers churn); only the *set of
    services* is immutable.
    """

    event_bus: AbstractEventBus
    graph: AbstractTaskGraph
    worker_runtime: AbstractWorkerRuntime
    result_store: AbstractResultStore
    scheduler: ExecutionScheduler
    settings: KernelSettings
    logger: logging.Logger

    @classmethod
    def in_memory(
        cls,
        settings: Optional[KernelSettings] = None,
        *,
        event_bus: Optional[AbstractEventBus] = None,
        graph: Optional[AbstractTaskGraph] = None,
        worker_runtime: Optional[AbstractWorkerRuntime] = None,
        result_store: Optional[AbstractResultStore] = None,
        scheduler: Optional[ExecutionScheduler] = None,
        logger: Optional[logging.Logger] = None,
    ) -> "KernelContext":
        """
        Build the default in-memory service graph, honouring any override.

        This is the single wiring site (the composition root's core). Pass an
        override to swap one subsystem — e.g. a Redis-backed graph or a
        transport-backed scheduler — and every other service stays in-memory
        with no other change.
        """
        # NB: use explicit ``is None`` checks, never ``x or Default()`` — the
        # graph/store define ``__len__``, so an *empty* injected instance is
        # falsy and ``or`` would silently discard it.
        if settings is None:
            settings = KernelSettings()
        if event_bus is None:
            event_bus = InMemoryEventBus()
        if graph is None:
            graph = InMemoryTaskGraph()
        if worker_runtime is None:
            worker_runtime = DefaultWorkerRuntime(
                event_bus=event_bus,
                heartbeat_timeout_seconds=settings.agent_offline_after_seconds,
            )
        if result_store is None:
            result_store = ResultStore()
        if scheduler is None:
            scheduler = ExecutionScheduler(
                graph,
                backend=LocalDispatchBackend(worker_runtime),
                event_bus=event_bus,
                result_store=result_store,
            )
        if logger is None:
            logger = logging.getLogger("agentos.kernel")
        return cls(
            event_bus=event_bus,
            graph=graph,
            worker_runtime=worker_runtime,
            result_store=result_store,
            scheduler=scheduler,
            settings=settings,
            logger=logger,
        )
