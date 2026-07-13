"""
kernel — the AgentOS composition root and runtime heartbeat.

The Kernel coordinates; it is deliberately unintelligent. It owns the object
graph (via :class:`KernelContext`), runs the lifecycle, and turns the loop as
discrete ticks. Concrete implementations are chosen only inside the context, so
swapping to Redis/Kafka/distributed backends touches this package alone.

Quick start
-----------
>>> from kernel import build_kernel
>>> from agents.coding import CodingAgent
>>> from models.task import Task
>>> kernel = build_kernel().boot()
>>> kernel.register_agent(CodingAgent())
'CodingAgent-1'
>>> kernel.submit(Task(description="Implement X", required_capabilities=["code"]))
>>> results = kernel.run_until_idle()      # deterministic stepping
>>> # ...or run the threaded loop: kernel.run(); ...; kernel.stop()
"""

from kernel.context import KernelContext
from kernel.dispatcher import Dispatcher
from kernel.kernel import Kernel, build_kernel
from kernel.lifecycle import KernelState, Lifecycle
from kernel.tick import Tick, TickResult

__all__ = [
    "Kernel",
    "build_kernel",
    "KernelContext",
    "Dispatcher",
    "KernelState",
    "Lifecycle",
    "Tick",
    "TickResult",
]
