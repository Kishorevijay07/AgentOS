"""
Run the AgentOS coordinator against a Redis broker.

Plans a goal, compiles it into a task DAG, and dispatches it to whatever worker
nodes are registered on the broker — workers running in **other processes or on
other machines** (start them with ``scripts/run_redis_worker.py``).

    # terminal 1..n:
    python scripts/run_redis_worker.py research
    python scripts/run_redis_worker.py coding
    python scripts/run_redis_worker.py testing
    python scripts/run_redis_worker.py documentation

    # terminal 0:
    python scripts/run_redis_coordinator.py "Build a REST API for a blog"

Requires a reachable Redis server (default ``redis://localhost:6379/0``;
override with REDIS_URL).
"""
from __future__ import annotations

import logging
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from distributed import DistributedScheduler, RedisTransport, WorkerDirectory
from planning import TemplatePlanner
from planning.models import Goal
from result_store import ResultStore
from task_graph import PlanGraphBuilder


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s  %(message)s")
    goal = sys.argv[1] if len(sys.argv) > 1 else "Build a REST API for a blog"

    transport = RedisTransport(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    transport.start()
    directory = WorkerDirectory(transport)
    directory.start()

    # Give already-running workers a moment to (re)announce via heartbeat.
    print("Discovering workers…")
    time.sleep(2.0)
    workers = directory.available_workers()
    if not workers:
        print("No workers registered. Start some with scripts/run_redis_worker.py")
        transport.stop()
        return 1
    for w in workers:
        print(f"  found {w.worker_id}  caps={w.capabilities}")

    plan = TemplatePlanner().plan(Goal(description=goal))
    graph = PlanGraphBuilder().build(plan)
    store = ResultStore()

    scheduler = DistributedScheduler(graph, directory, transport, result_store=store)
    scheduler.start()
    scheduler.run_until_idle(timeout=120.0)

    print("\n" + "=" * 72)
    for record in store.all():
        status = "OK " if record.success else "FAIL"
        print(f"[{status}] {record.agent_id:<20} {str(record.output)[:120]}")
    done, total = len(graph.completed_tasks()), len(graph.nodes())
    print(f"\nCompleted {done}/{total} tasks across remote workers.")

    scheduler.stop()
    transport.stop()
    return 0 if done == total else 2


if __name__ == "__main__":
    raise SystemExit(main())
