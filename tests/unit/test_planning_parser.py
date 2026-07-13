"""Unit tests for JSONPlanParser — the untrusted-text → Plan boundary."""
from __future__ import annotations

import pytest

from planning.errors import PlanParseError
from planning.models import Goal
from planning.parser import JSONPlanParser


@pytest.fixture()
def parser() -> JSONPlanParser:
    return JSONPlanParser()


@pytest.fixture()
def goal() -> Goal:
    return Goal(description="Build a REST API for a blog")


class TestHappyPath:
    def test_plain_json_array(self, parser, goal):
        raw = '[{"description": "Design API", "capabilities": ["code"], "depends_on": []}]'
        plan = parser.parse(raw, goal)
        assert len(plan) == 1
        assert plan.steps[0].description == "Design API"
        assert plan.steps[0].order == 1
        assert plan.steps[0].capabilities == ["code"]

    def test_code_fenced_json(self, parser, goal):
        raw = '```json\n[{"description": "Step one"}, {"description": "Step two"}]\n```'
        plan = parser.parse(raw, goal)
        assert [s.order for s in plan.steps] == [1, 2]

    def test_prose_wrapped_json(self, parser, goal):
        raw = 'Sure! Here is the plan:\n[{"description": "Only step"}]\nHope that helps.'
        plan = parser.parse(raw, goal)
        assert len(plan) == 1

    def test_object_with_steps_key(self, parser, goal):
        raw = '{"steps": [{"description": "A"}, {"description": "B"}]}'
        plan = parser.parse(raw, goal)
        assert len(plan) == 2

    def test_list_of_plain_strings(self, parser, goal):
        raw = '["Design API", "Write tests"]'
        plan = parser.parse(raw, goal)
        assert plan.steps[0].description == "Design API"
        assert plan.steps[1].order == 2

    def test_explicit_order_and_deps_preserved(self, parser, goal):
        raw = (
            '[{"order": 1, "description": "A"}, '
            '{"order": 2, "description": "B", "depends_on": [1]}]'
        )
        plan = parser.parse(raw, goal)
        assert plan.steps[1].depends_on == [1]


class TestFailures:
    def test_empty_output_raises(self, parser, goal):
        with pytest.raises(PlanParseError):
            parser.parse("   ", goal)

    def test_invalid_json_raises(self, parser, goal):
        with pytest.raises(PlanParseError):
            parser.parse("not json at all", goal)

    def test_non_list_payload_raises(self, parser, goal):
        with pytest.raises(PlanParseError):
            parser.parse('{"foo": "bar"}', goal)

    def test_step_without_description_raises(self, parser, goal):
        with pytest.raises(PlanParseError):
            parser.parse('[{"capabilities": ["code"]}]', goal)
