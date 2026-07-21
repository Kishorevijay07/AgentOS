"""Unit tests for the reflection strategies (Heuristic + LLM)."""
from __future__ import annotations

from uuid import uuid4

from reflection.models import ReflectionRequest, ReflectionVerdict
from reflection.reflector import HeuristicReflector, LLMReflector
from services.llm import StaticLLMClient


def _req(output: str, *, success: bool = True, caps=("code",)) -> ReflectionRequest:
    return ReflectionRequest(
        task_id=uuid4(),
        description="Implement the widget",
        output=output,
        success=success,
        allowed_capabilities=list(caps),
    )


class TestHeuristicReflector:
    def test_accepts_substantive_output(self):
        decision = HeuristicReflector().reflect(_req("a fully-formed, long enough answer"))
        assert decision.verdict == ReflectionVerdict.ACCEPT

    def test_replans_on_trivial_output(self):
        decision = HeuristicReflector().reflect(_req("no"))
        assert decision.verdict == ReflectionVerdict.REPLAN
        assert len(decision.new_tasks) == 1
        assert decision.new_tasks[0].capabilities == ["code"]

    def test_replans_on_failure(self):
        decision = HeuristicReflector().reflect(_req("", success=False))
        assert decision.verdict == ReflectionVerdict.REPLAN


class TestLLMReflector:
    def test_parses_replan_decision(self):
        canned = (
            '{"verdict": "replan", "reason": "missing tests", '
            '"new_tasks": [{"description": "Add unit tests", "capabilities": ["test"]}]}'
        )
        decision = LLMReflector(StaticLLMClient(canned)).reflect(_req("some code"))
        assert decision.verdict == ReflectionVerdict.REPLAN
        assert decision.new_tasks[0].description == "Add unit tests"
        assert decision.new_tasks[0].capabilities == ["test"]

    def test_parses_accept_decision(self):
        decision = LLMReflector(StaticLLMClient('{"verdict": "accept"}')).reflect(_req("x"))
        assert decision.verdict == ReflectionVerdict.ACCEPT

    def test_prompt_includes_task_and_output(self):
        client = StaticLLMClient('{"verdict": "accept"}')
        LLMReflector(client).reflect(_req("the produced output", caps=["code", "test"]))
        prompt = client.calls[0]
        assert "Implement the widget" in prompt
        assert "the produced output" in prompt
        assert "code" in prompt and "test" in prompt  # capability hint

    def test_fail_open_on_backend_error(self):
        class _BoomLLM:
            def complete(self, prompt): raise ConnectionError("down")

        decision = LLMReflector(_BoomLLM()).reflect(_req("x"))
        assert decision.verdict == ReflectionVerdict.ACCEPT  # never crashes the run

    def test_fail_open_on_garbage_output(self):
        decision = LLMReflector(StaticLLMClient("not json at all")).reflect(_req("x"))
        assert decision.verdict == ReflectionVerdict.ACCEPT

    def test_replan_without_tasks_degrades_to_accept(self):
        decision = LLMReflector(
            StaticLLMClient('{"verdict": "replan", "new_tasks": []}')
        ).reflect(_req("x"))
        assert decision.verdict == ReflectionVerdict.ACCEPT
