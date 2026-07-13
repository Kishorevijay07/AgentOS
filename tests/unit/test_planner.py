"""Unit tests for the Planner strategies (TemplatePlanner, LLMPlanner)."""
from __future__ import annotations

import pytest

from planning.errors import PlanGenerationError, PlanParseError
from planning.models import Goal
from planning.planner import LLMPlanner, StepTemplate, TemplatePlanner
from services.llm import StaticLLMClient


class _BoomLLM:
    def complete(self, prompt: str) -> str:
        raise ConnectionError("model unreachable")


class TestTemplatePlanner:
    def test_default_lifecycle_plan(self):
        plan = TemplatePlanner().plan(Goal(description="Build a blog API"))
        assert len(plan) == 5
        # Goal is interpolated into each description.
        assert all("Build a blog API" in s.description for s in plan.steps)
        # Linear dependency chain.
        assert plan.steps[0].depends_on == []
        assert plan.steps[1].depends_on == [1]
        assert plan.steps[4].depends_on == [4]

    def test_custom_templates(self):
        planner = TemplatePlanner(
            templates=[
                StepTemplate("Research {goal}", ["research"]),
                StepTemplate("Ship {goal}", ["code"], depends_on=(1,)),
            ]
        )
        plan = planner.plan(Goal(description="X"))
        assert len(plan) == 2
        assert plan.steps[1].capabilities == ["code"]
        assert plan.steps[1].depends_on == [1]


class TestLLMPlanner:
    def test_parses_llm_json_into_plan(self):
        canned = (
            '[{"description": "Design API", "capabilities": ["code"]},'
            ' {"description": "Create schema", "capabilities": ["code"], "depends_on": [1]}]'
        )
        planner = LLMPlanner(StaticLLMClient(canned))
        plan = planner.plan(Goal(description="Build a REST API for a blog"))
        assert len(plan) == 2
        assert plan.steps[0].description == "Design API"
        assert plan.steps[1].depends_on == [1]

    def test_prompt_is_rendered_and_sent(self):
        client = StaticLLMClient('[{"description": "A"}]')
        LLMPlanner(client).plan(Goal(description="Do a thing", context="be terse"))
        assert len(client.calls) == 1
        assert "Do a thing" in client.calls[0]
        assert "be terse" in client.calls[0]

    def test_backend_failure_becomes_generation_error(self):
        planner = LLMPlanner(_BoomLLM())
        with pytest.raises(PlanGenerationError):
            planner.plan(Goal(description="X"))

    def test_unparseable_output_raises_parse_error(self):
        planner = LLMPlanner(StaticLLMClient("this is not json"))
        with pytest.raises(PlanParseError):
            planner.plan(Goal(description="X"))
