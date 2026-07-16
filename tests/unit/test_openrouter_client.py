"""Unit tests for OpenRouterLLMClient — mocked transport, no network."""
from __future__ import annotations

import json
from typing import Any, List, Optional

import pytest
import requests

from services.llm import LLMClient
from services.openrouter import LLMClientError, OpenRouterLLMClient


class _FakeResponse:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body) if not isinstance(body, str) else body

    def json(self) -> Any:
        if isinstance(self._body, str):
            raise ValueError("not json")
        return self._body


class _FakeSession:
    """Scripted stand-in for requests.Session — returns queued responses."""

    def __init__(self, responses: List[Any]) -> None:
        self._responses = list(responses)
        self.calls: List[dict] = []

    def post(self, url: str, *, json: dict, headers: dict, timeout: float) -> _FakeResponse:
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _ok(content: str = "hello") -> _FakeResponse:
    return _FakeResponse(200, {"choices": [{"message": {"content": content}}]})


def _client(responses: List[Any], **kw) -> OpenRouterLLMClient:
    session = _FakeSession(responses)
    client = OpenRouterLLMClient("test-key", session=session, max_retries=3, **kw)
    client._session_for_test = session  # type: ignore[attr-defined]
    return client


class TestSuccess:
    def test_satisfies_llm_client_protocol(self):
        assert isinstance(_client([_ok()]), LLMClient)

    def test_returns_completion_text(self):
        assert _client([_ok("plan text")]).complete("prompt") == "plan text"

    def test_sends_model_prompt_and_auth(self):
        client = _client([_ok()], model="openai/gpt-4o-mini")
        client.complete("do the thing")
        call = client._session_for_test.calls[0]  # type: ignore[attr-defined]
        assert call["json"]["model"] == "openai/gpt-4o-mini"
        assert call["json"]["messages"] == [{"role": "user", "content": "do the thing"}]
        assert call["headers"]["Authorization"] == "Bearer test-key"


class TestRetries:
    def test_retries_on_429_then_succeeds(self, monkeypatch):
        monkeypatch.setattr("services.openrouter.time.sleep", lambda s: None)
        client = _client([_FakeResponse(429, "rate limited"), _ok("after retry")])
        assert client.complete("p") == "after retry"

    def test_retries_on_transport_error(self, monkeypatch):
        monkeypatch.setattr("services.openrouter.time.sleep", lambda s: None)
        client = _client([requests.ConnectionError("down"), _ok("recovered")])
        assert client.complete("p") == "recovered"

    def test_exhausted_retries_raise(self, monkeypatch):
        monkeypatch.setattr("services.openrouter.time.sleep", lambda s: None)
        client = _client([_FakeResponse(503, "unavailable")] * 3)
        with pytest.raises(LLMClientError, match="after 3 attempts"):
            client.complete("p")


class TestFailFast:
    def test_auth_error_is_not_retried(self):
        client = _client([_FakeResponse(401, "bad key")])
        with pytest.raises(LLMClientError, match="401"):
            client.complete("p")
        assert len(client._session_for_test.calls) == 1  # type: ignore[attr-defined]

    def test_empty_api_key_rejected_at_construction(self):
        with pytest.raises(LLMClientError, match="API key"):
            OpenRouterLLMClient("   ")

    def test_malformed_response_shape_raises(self):
        client = _client([_FakeResponse(200, {"unexpected": True})])
        with pytest.raises(LLMClientError, match="unexpected response shape"):
            client.complete("p")

    def test_empty_completion_raises(self):
        client = _client([_FakeResponse(200, {"choices": [{"message": {"content": "  "}}]})])
        with pytest.raises(LLMClientError, match="empty completion"):
            client.complete("p")


class TestFromEnv:
    def test_from_env_reads_key_and_model(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
        monkeypatch.setenv("OPENROUTER_MODEL", "meta-llama/llama-3-70b")
        client = OpenRouterLLMClient.from_env()
        assert client._model == "meta-llama/llama-3-70b"

    def test_from_env_without_key_raises(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
        with pytest.raises(LLMClientError, match="API key"):
            OpenRouterLLMClient.from_env()
