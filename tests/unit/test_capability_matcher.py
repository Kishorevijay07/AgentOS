"""Unit tests for DefaultCapabilityMatcher (capability-only placement)."""
from __future__ import annotations

from typing import List

from runtime.handle import WorkerHandle
from scheduling.capability import DefaultCapabilityMatcher


class _W:
    def __init__(self, caps: List[str]) -> None:
        self.capabilities = caps

    def initialize(self): ...
    def execute(self, task): ...
    def heartbeat(self): ...
    def pause(self): ...
    def resume(self): ...
    def shutdown(self): ...


def handle(wid: str, caps: List[str]) -> WorkerHandle:
    return WorkerHandle(wid, _W(caps))


class TestMatching:
    def setup_method(self):
        self.matcher = DefaultCapabilityMatcher()

    def test_full_match_prefers_most_specialised(self):
        generalist = handle("gen", ["code", "test", "research"])
        specialist = handle("spec", ["code"])
        chosen = self.matcher.match(["code"], [generalist, specialist])
        assert chosen.worker_id == "spec"

    def test_partial_fallback_when_no_full_match(self):
        h = handle("h", ["code"])
        chosen = self.matcher.match(["code", "test"], [h])
        assert chosen.worker_id == "h"  # covers 1 of 2 → best available

    def test_no_match_returns_none(self):
        h = handle("h", ["code"])
        assert self.matcher.match(["gpu"], [h]) is None

    def test_empty_requirement_returns_most_specialised(self):
        a = handle("a", ["code", "test"])
        b = handle("b", ["code"])
        assert self.matcher.match([], [a, b]).worker_id == "b"

    def test_empty_candidates_returns_none(self):
        assert self.matcher.match(["code"], []) is None
