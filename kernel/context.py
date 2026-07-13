from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from agents.registry import AbstractAgentRegistry, AgentRegistry
from config.settings import KernelSettings
from events.bus import AbstractEventBus, InMemoryEventBus
from result_store import AbstractResultStore, ResultStore
from scheduler.scheduler import Scheduler
from task_queue.result_queue import AbstractResultQueue, ResultQueue
from task_queue.task_queue import AbstractTaskQueue, TaskQueue


@dataclass(frozen=True)
class KernelContext:
    """
    Shared-services container passed to every runtime module.

    Rather than threading ``queue``, ``scheduler``, ``registry``, ``event_bus``,
    and ``logger`` through every constructor, the Kernel builds **one**
    ``KernelContext`` and hands the same object to the ``Dispatcher``, the
    ``Tick``, and anything else that needs runtime services. This is the
    dependency-injection container many mature frameworks use.

    Every field is typed as its **abstraction** (see ADR-0008), so the whole
    graph can be pointed at Redis/Kafka backends by passing overrides to
    :meth:`in_memory` — no downstream module names a concrete type.

    Frozen: the wiring is fixed for a Kernel's lifetime. The services it holds
    are themselves mutable (queues fill, the registry changes); only the *set of
    services* is immutable.

    Intentionally **excluded**: memory / LLM services. Those modules are still
    stubs — they join the context when they exist (the "plug in intelligence"
    sprint), not before.
    """

    event_bus: AbstractEventBus
    registry: AbstractAgentRegistry
    task_queue: AbstractTaskQueue
    result_queue: AbstractResultQueue
    result_store: AbstractResultStore
    scheduler: Scheduler
    settings: KernelSettings
    logger: logging.Logger

    @classmethod
    def in_memory(
        cls,
        settings: Optional[KernelSettings] = None,
        *,
        event_bus: Optional[AbstractEventBus] = None,
        registry: Optional[AbstractAgentRegistry] = None,
        task_queue: Optional[AbstractTaskQueue] = None,
        result_queue: Optional[AbstractResultQueue] = None,
        result_store: Optional[AbstractResultStore] = None,
        scheduler: Optional[Scheduler] = None,
        logger: Optional[logging.Logger] = None,
    ) -> "KernelContext":
        """
        Build the default in-memory service graph, honouring any override.

        This is the single wiring site (the composition root's core). Pass an
        override to swap one subsystem — e.g.
        ``KernelContext.in_memory(event_bus=RedisEventBus(...))`` — and every
        other service stays in-memory with no other change.
        """
        # NB: use explicit ``is None`` checks, never ``x or Default()`` — the
        # queues/registry/store define ``__len__``, so an *empty* injected
        # instance is falsy and ``or`` would silently discard it.
        if settings is None:
            settings = KernelSettings()
        if event_bus is None:
            event_bus = InMemoryEventBus()
        if registry is None:
            registry = AgentRegistry()
        if task_queue is None:
            task_queue = TaskQueue()
        if result_queue is None:
            result_queue = ResultQueue()
        if result_store is None:
            result_store = ResultStore()
        if scheduler is None:
            # Scheduler depends only on the registry + bus abstractions above.
            scheduler = Scheduler(registry=registry, bus=event_bus)
        if logger is None:
            logger = logging.getLogger("agentos.kernel")
        return cls(
            event_bus=event_bus,
            registry=registry,
            task_queue=task_queue,
            result_queue=result_queue,
            result_store=result_store,
            scheduler=scheduler,
            settings=settings,
            logger=logger,
        )
