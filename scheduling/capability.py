from __future__ import annotations

from typing import List, Optional, Protocol, Sequence, TypeVar, runtime_checkable


@runtime_checkable
class HasCapabilities(Protocol):
    """
    Anything the matcher can place work on: an id plus capability tags.

    Both the local runtime's ``WorkerHandle`` and the distributed layer's
    ``RemoteWorkerInfo`` satisfy this structurally — which is exactly what lets
    one matcher serve both dispatch backends without knowing where a worker
    lives.
    """

    worker_id: str
    capabilities: List[str]


C = TypeVar("C", bound=HasCapabilities)


@runtime_checkable
class CapabilityMatcher(Protocol):
    """
    Strategy that places a task on a worker by **capabilities alone**.

    This is the component that makes ``if task == "research"`` impossible: it is
    given the task's required capability tags and a set of candidate workers, and
    returns the best fit — or ``None`` if none can satisfy the requirement. Swap
    it for a cost-aware, locality-aware, or load-aware matcher without touching
    the scheduler.
    """

    def match(self, required: Sequence[str], candidates: Sequence[C]) -> Optional[C]:
        """Return the best candidate for *required*, or ``None``."""
        ...


class DefaultCapabilityMatcher:
    """
    Superset-match with a "most-specialised wins" tie-break and a partial fallback.

    1. **Full match** — a worker whose capabilities are a superset of *required*.
       Among full matches, prefer the **most specialised** worker (fewest total
       capabilities), leaving generalists free for tasks that need them.
    2. **Partial fallback** — if nothing fully matches, pick the worker covering
       the most required capabilities (``0`` ⇒ no match ⇒ ``None``).

    This mirrors the placement policy AgentOS has used since the first scheduler,
    extracted here as an injectable strategy.
    """

    def match(self, required: Sequence[str], candidates: Sequence[C]) -> Optional[C]:
        if not candidates:
            return None
        req = set(required)
        if not req:
            # No requirement → any worker; prefer the most specialised.
            return min(candidates, key=lambda h: len(h.capabilities))

        full = [h for h in candidates if req.issubset(set(h.capabilities))]
        if full:
            return min(full, key=lambda h: len(h.capabilities))

        best = max(candidates, key=lambda h: len(req & set(h.capabilities)))
        return best if req & set(best.capabilities) else None
