"""
Start one AgentOS worker node connected to a Redis broker.

Run one of these per terminal / machine / container — each is a real,
separate OS process hosting one worker:

    python scripts/run_redis_worker.py coding            # CodingAgent
    python scripts/run_redis_worker.py research          # ResearchAgent
    python scripts/run_redis_worker.py testing tester-2  # custom worker id

Requires a reachable Redis server (default ``redis://localhost:6379/0``;
override with the REDIS_URL environment variable). Start one with Docker:

    docker run --rm -p 6379:6379 redis:7
"""
from __future__ import annotations

import logging
import os
import pathlib
import signal
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from agents.coding import CodingAgent
from agents.documentation import DocumentationAgent
from agents.research import ResearchAgent
from agents.testing import TestingAgent
from distributed import RedisTransport, RemoteWorkerNode

_AGENTS = {
    "coding": CodingAgent,
    "research": ResearchAgent,
    "testing": TestingAgent,
    "documentation": DocumentationAgent,
}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s  %(message)s")
    kind = (sys.argv[1] if len(sys.argv) > 1 else "coding").lower()
    if kind not in _AGENTS:
        print(f"Unknown worker kind {kind!r}. Choose from: {', '.join(_AGENTS)}")
        return 1
    worker_id = sys.argv[2] if len(sys.argv) > 2 else f"{kind}-{os.getpid()}"

    transport = RedisTransport(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    transport.start()
    node = RemoteWorkerNode(_AGENTS[kind](), transport, worker_id=worker_id)
    node.start()
    print(f"Worker {worker_id!r} online (Ctrl+C to stop).")

    stop = [False]
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__(0, True))
    try:
        while not stop[0]:
            time.sleep(0.5)  # portable idle loop (Windows has no signal.pause)
    finally:
        node.stop()
        transport.stop()
        print(f"Worker {worker_id!r} stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
