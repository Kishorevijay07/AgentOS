from __future__ import annotations

from typing import List

from typing_extensions import TypedDict


class GraphState(TypedDict):
    """
    Immutable snapshot shared across every LangGraph node.

    LangGraph merges each node's return dict into this state after the node
    completes.  A node only needs to return the keys it actually changed.

    Attributes
    ----------
    goal:
        The high-level natural-language goal handed to the Planner node.
    pending_count:
        Number of tasks that still remain in the ``TaskQueue``.
        Updated after every Supervisor pass.
    results:
        Accumulated output strings from all completed tasks.
        Appended to by the Reflection node after each drain.
    retry_count:
        Running total of reflection-triggered retry tasks injected back
        into the queue.  Useful for detecting run-away retry loops.
    done:
        Terminal flag.  Set to ``True`` by the Reflection node once the
        ``TaskQueue`` is completely empty.  Drives the conditional edge
        that decides whether to loop back to the Supervisor or END.
    """

    goal: str
    pending_count: int
    results: List[str]
    retry_count: int
    done: bool
