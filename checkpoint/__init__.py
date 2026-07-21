"""
checkpoint — durable snapshots for crash-resumable runs (v0.9).

Snapshots a run's execution state (the task graph — which carries per-task
execution history — plus the reflection budget and tick counter) so a long
autonomous run survives a crash and resumes exactly where it left off.
Interrupted (in-flight) tasks are re-run on restore.

Quick start
-----------
>>> from checkpoint import FileCheckpointStore
>>> store = FileCheckpointStore("run.checkpoint.json")
>>> kernel.save_checkpoint(store)          # persist
>>> # ... process dies, restart ...
>>> fresh = build_kernel(); fresh.register_agent(...); fresh.boot()
>>> fresh.load_checkpoint(store)           # resume
>>> fresh.run_until_idle()
"""

from checkpoint.models import Checkpoint
from checkpoint.redis_store import RedisCheckpointStore
from checkpoint.store import (
    CheckpointStore,
    FileCheckpointStore,
    InMemoryCheckpointStore,
)

__all__ = [
    "Checkpoint",
    "CheckpointStore",
    "FileCheckpointStore",
    "InMemoryCheckpointStore",
    "RedisCheckpointStore",
]
