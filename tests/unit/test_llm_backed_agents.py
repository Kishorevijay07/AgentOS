"""Unit tests for LLM-backed workers (injected intelligence, offline fallback)."""
from __future__ import annotations

from agents.coding import CodingAgent
from agents.research import ResearchAgent
from models.task import Task
from services.llm import StaticLLMClient


class TestLLMInjection:
    def test_coding_agent_uses_injected_llm(self):
        llm = StaticLLMClient("def add(a, b):\n    return a + b")
        agent = CodingAgent(llm=llm)
        out = agent.execute(Task(description="Write an add function"))
        assert "def add" in out
        # The task description made it into the prompt.
        assert "Write an add function" in llm.calls[0]

    def test_research_agent_uses_injected_llm(self):
        llm = StaticLLMClient("## Findings\n- point one")
        agent = ResearchAgent(llm=llm)
        out = agent.execute(Task(description="Research REST API best practices"))
        assert "Findings" in out
        assert "REST API best practices" in llm.calls[0]

    def test_without_llm_falls_back_to_placeholder(self):
        assert "[CodingAgent] Executed:" in CodingAgent().execute(Task(description="x"))
        assert "[ResearchAgent] Executed:" in ResearchAgent().execute(Task(description="x"))


class TestEndToEndWithFakeLLM:
    def test_llm_backed_worker_runs_through_the_runtime(self):
        """An LLM-backed agent works inside the full runtime path."""
        from runtime import DefaultWorkerRuntime

        runtime = DefaultWorkerRuntime()
        wid = runtime.register_worker(CodingAgent(llm=StaticLLMClient("real output")))
        outcome = runtime.execute_task(
            wid, Task(description="implement", required_capabilities=["code"])
        )
        assert outcome.success is True
        assert outcome.output == "real output"
        runtime.shutdown()
