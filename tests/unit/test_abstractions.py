"""
Unit tests for the program-to-abstractions seam (ADR-0008).

Verifies that every swappable subsystem is interface-backed:
  * each concrete is a subclass of its abstraction,
  * each abstraction is a genuine ABC (cannot be instantiated directly),
  * the KernelContext and unified scheduler accept abstractions (DIP).
"""
from __future__ import annotations

import pytest

from agents.registry import AbstractAgentRegistry, AgentRegistry
from kernel.context import KernelContext
from result_store import AbstractResultStore, ResultStore
from result_store.store import AbstractResultStore as AbstractResultStoreDirect
from runtime import AbstractWorkerRuntime, DefaultWorkerRuntime
from scheduler.scheduler import Scheduler
from scheduling import DispatchBackend, ExecutionScheduler, LocalDispatchBackend
from task_graph import AbstractTaskGraph, InMemoryTaskGraph
from task_queue import (
    AbstractResultQueue,
    AbstractTaskQueue,
    ResultQueue,
    TaskQueue,
)


ABSTRACTION_PAIRS = [
    (AbstractAgentRegistry, AgentRegistry),
    (AbstractTaskQueue, TaskQueue),
    (AbstractResultQueue, ResultQueue),
    (AbstractResultStore, ResultStore),
    (AbstractTaskGraph, InMemoryTaskGraph),
    (AbstractWorkerRuntime, DefaultWorkerRuntime),
]


class TestConcretesImplementAbstractions:
    @pytest.mark.parametrize("abstract, concrete", ABSTRACTION_PAIRS)
    def test_concrete_is_subclass_of_abstract(self, abstract, concrete):
        assert issubclass(concrete, abstract)

    @pytest.mark.parametrize("abstract, concrete", ABSTRACTION_PAIRS)
    def test_instance_is_instance_of_abstract(self, abstract, concrete):
        assert isinstance(concrete(), abstract)

    @pytest.mark.parametrize("abstract, _concrete", ABSTRACTION_PAIRS)
    def test_abstract_cannot_be_instantiated(self, abstract, _concrete):
        with pytest.raises(TypeError):
            abstract()  # ABC with abstract methods → not instantiable

    def test_result_store_export_matches_module_definition(self):
        # The package export and the in-module definition are the same object.
        assert AbstractResultStore is AbstractResultStoreDirect


class TestKernelContextV2:
    def test_context_fields_are_abstraction_typed(self):
        context = KernelContext.in_memory(
            graph=InMemoryTaskGraph(),
            result_store=ResultStore(),
        )
        assert isinstance(context.graph, AbstractTaskGraph)
        assert isinstance(context.worker_runtime, AbstractWorkerRuntime)
        assert isinstance(context.result_store, AbstractResultStore)
        assert isinstance(context.scheduler, ExecutionScheduler)
        context.worker_runtime.shutdown()

    def test_empty_injected_graph_is_not_discarded(self):
        """Regression: empty containers are falsy — `or` wiring loses them."""
        graph = InMemoryTaskGraph()
        context = KernelContext.in_memory(graph=graph)
        assert context.graph is graph
        context.worker_runtime.shutdown()


class TestSwapSeam:
    """A fake implementation of an abstraction must be injectable unchanged."""

    def test_fake_registry_satisfies_legacy_scheduler(self):
        class FakeRegistry(AbstractAgentRegistry):
            def register(self, agent): return "fake-1"
            def remove(self, agent_id): ...
            def heartbeat(self, agent_id): ...
            def get_capabilities(self, agent_id): return []
            def get_status(self, agent_id): ...
            def set_status(self, agent_id, status): ...
            def get_current_task(self, agent_id): return None
            def set_current_task(self, agent_id, task_id): ...
            def list_agents(self): return []
            def available_agents(self): return []
            def mark_offline(self, agent_id): ...

        from models.task import Task

        scheduler = Scheduler(registry=FakeRegistry())
        assert scheduler.dispatch(Task(description="x")) is None

    def test_fake_dispatch_backend_satisfies_unified_scheduler(self):
        """The v0.7 seam: any DispatchBackend drops into ExecutionScheduler."""

        class NullBackend(DispatchBackend):
            def candidates(self): return []
            def dispatch(self, task, worker_id, *, execution_id=None, timeout=None): ...

        scheduler = ExecutionScheduler(InMemoryTaskGraph(), backend=NullBackend())
        assert scheduler.schedule_wave() == 0  # no candidates → nothing placed

    def test_local_backend_wraps_runtime(self):
        runtime = DefaultWorkerRuntime()
        backend = LocalDispatchBackend(runtime)
        assert isinstance(backend, DispatchBackend)
        assert backend.candidates() == []
        runtime.shutdown()
