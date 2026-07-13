"""
Unit tests for the program-to-abstractions seam (ADR-0008).

Verifies that every swappable subsystem is interface-backed:
  * each concrete is a subclass of its abstraction,
  * each abstraction is a genuine ABC (cannot be instantiated directly),
  * the Scheduler and Dispatcher accept the abstractions (Dependency Inversion).
"""
from __future__ import annotations

import pytest

from agents.registry import AbstractAgentRegistry, AgentRegistry
from kernel.context import KernelContext
from kernel.dispatcher import Dispatcher
from result_store import AbstractResultStore, ResultStore
from result_store.store import AbstractResultStore as AbstractResultStoreDirect
from scheduler.scheduler import Scheduler
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


class TestOrchestratorsAcceptAbstractions:
    def test_scheduler_accepts_registry_abstraction(self):
        registry: AbstractAgentRegistry = AgentRegistry()
        scheduler = Scheduler(registry=registry)
        assert isinstance(scheduler, Scheduler)

    def test_dispatcher_accepts_abstraction_backed_context(self):
        # KernelContext holds every collaborator as its abstraction; the
        # Dispatcher depends only on the context.
        context = KernelContext.in_memory(
            task_queue=TaskQueue(),
            result_queue=ResultQueue(),
            result_store=ResultStore(),
            registry=AgentRegistry(),
        )
        dispatcher = Dispatcher(context)
        assert isinstance(dispatcher, Dispatcher)
        assert isinstance(context.task_queue, AbstractTaskQueue)
        assert isinstance(context.result_queue, AbstractResultQueue)
        assert isinstance(context.result_store, AbstractResultStore)
        assert isinstance(context.registry, AbstractAgentRegistry)


class TestSwapSeam:
    """A fake implementation of an abstraction must be injectable unchanged."""

    def test_fake_registry_satisfies_scheduler(self):
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

        # No capable agents → dispatch returns None, but the seam works.
        from models.task import Task

        scheduler = Scheduler(registry=FakeRegistry())
        assert scheduler.dispatch(Task(description="x")) is None
