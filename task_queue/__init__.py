"""
task_queue — AgentOS work-dispatch layer.

Two queues, each behind an abstraction so an in-memory backend can be swapped
for Redis/Kafka without touching the Supervisor or Kernel:

- :class:`AbstractTaskQueue` / :class:`TaskQueue` — priority + capability +
  dependency aware queue of pending :class:`~models.task.Task` objects.
- :class:`AbstractResultQueue` / :class:`ResultQueue` — decouples workers from
  the Supervisor; workers push results, the Supervisor drains them.
"""

from task_queue.result_queue import AbstractResultQueue, ResultQueue
from task_queue.task_queue import AbstractTaskQueue, TaskQueue

__all__ = [
    "AbstractTaskQueue",
    "TaskQueue",
    "AbstractResultQueue",
    "ResultQueue",
]
