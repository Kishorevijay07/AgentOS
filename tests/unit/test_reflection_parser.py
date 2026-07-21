"""Unit tests for ReflectionParser — fail-open JSON parsing."""
from __future__ import annotations

import pytest

from reflection.models import ReflectionVerdict
from reflection.parser import ReflectionParser


@pytest.fixture()
def parser() -> ReflectionParser:
    return ReflectionParser()


class TestHappyPath:
    def test_plain_accept(self, parser):
        d = parser.parse('{"verdict": "accept", "reason": "good"}')
        assert d.verdict == ReflectionVerdict.ACCEPT
        assert d.reason == "good"

    def test_replan_with_tasks(self, parser):
        d = parser.parse(
            '{"verdict": "replan", "new_tasks": '
            '[{"description": "do X", "capabilities": ["code"]}]}'
        )
        assert d.verdict == ReflectionVerdict.REPLAN
        assert d.new_tasks[0].description == "do X"

    def test_code_fenced(self, parser):
        d = parser.parse('```json\n{"verdict": "accept"}\n```')
        assert d.verdict == ReflectionVerdict.ACCEPT

    def test_prose_wrapped(self, parser):
        d = parser.parse('Here is my judgement: {"verdict": "accept"} — done.')
        assert d.verdict == ReflectionVerdict.ACCEPT

    def test_string_tasks_accepted(self, parser):
        d = parser.parse('{"verdict": "replan", "new_tasks": ["fix the bug"]}')
        assert d.new_tasks[0].description == "fix the bug"


class TestFailOpen:
    def test_garbage_accepts(self, parser):
        assert parser.parse("not json").verdict == ReflectionVerdict.ACCEPT

    def test_empty_accepts(self, parser):
        assert parser.parse("   ").verdict == ReflectionVerdict.ACCEPT

    def test_non_object_accepts(self, parser):
        assert parser.parse("[1, 2, 3]").verdict == ReflectionVerdict.ACCEPT

    def test_replan_with_no_tasks_degrades_to_accept(self, parser):
        assert parser.parse('{"verdict": "replan"}').verdict == ReflectionVerdict.ACCEPT
