from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger("agentos.services.llm")

#: OpenRouter's OpenAI-compatible chat-completions endpoint.
_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
#: A capable, inexpensive default; override via OPENROUTER_MODEL or the ctor.
_DEFAULT_MODEL = "openai/gpt-4o-mini"

#: HTTP statuses worth retrying (rate limit + transient server errors).
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class LLMClientError(Exception):
    """
    Raised when the LLM backend fails after all retries.

    Consumers that use the planner never see this directly — ``LLMPlanner``
    normalises any client exception into a ``PlanGenerationError`` — but direct
    callers (LLM-backed workers) can catch it explicitly.
    """


class OpenRouterLLMClient:
    """
    :class:`~services.llm.LLMClient` implementation backed by OpenRouter.

    OpenRouter exposes an OpenAI-compatible ``/chat/completions`` API that
    fronts many models (OpenAI, Anthropic, Google, Meta, …), so this single
    client gives AgentOS model choice via one env var. It satisfies the
    ``LLMClient`` Protocol structurally — nothing else in the codebase changes
    (ADR-0008: program to abstractions).

    Resilience: a per-request ``timeout`` and bounded retries with exponential
    backoff on 429/5xx and transport errors. Everything else fails fast with
    :class:`LLMClientError` (auth errors won't fix themselves by retrying).

    Configuration
    -------------
    Explicit constructor args win; otherwise :meth:`from_env` reads:

    * ``OPENROUTER_API_KEY`` — required.
    * ``OPENROUTER_MODEL``  — optional, defaults to ``openai/gpt-4o-mini``.

    Never hardcode the key; put it in ``.env`` (see ``.env.example``).
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str = _DEFAULT_MODEL,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = 60.0,
        max_retries: int = 3,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        session: Optional[requests.Session] = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise LLMClientError(
                "OpenRouter API key is empty. Set OPENROUTER_API_KEY in your .env "
                "or pass api_key explicitly."
            )
        self._api_key = api_key.strip()
        self._model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._timeout = timeout
        self._max_retries = max_retries
        self._temperature = temperature
        self._max_tokens = max_tokens
        # An injectable Session is the test seam (mock transport, no network).
        self._session = session or requests.Session()

    # ------------------------------------------------------------------ #
    #  Construction helpers
    # ------------------------------------------------------------------ #

    @classmethod
    def from_env(cls, **overrides: Any) -> "OpenRouterLLMClient":
        """
        Build a client from environment variables (loading ``.env`` first).

        Keyword overrides are passed through to the constructor, so e.g.
        ``OpenRouterLLMClient.from_env(model="anthropic/claude-sonnet-4")``
        keeps the env key but pins a model.
        """
        try:
            from dotenv import load_dotenv

            load_dotenv()  # no-op if no .env file is present
        except ImportError:  # pragma: no cover - dotenv is in requirements
            pass

        api_key = os.getenv("OPENROUTER_API_KEY", "")
        model = overrides.pop("model", None) or os.getenv("OPENROUTER_MODEL", _DEFAULT_MODEL)
        return cls(api_key, model=model, **overrides)

    # ------------------------------------------------------------------ #
    #  LLMClient protocol
    # ------------------------------------------------------------------ #

    def complete(self, prompt: str) -> str:
        """
        Return the model's completion for *prompt*.

        Raises
        ------
        LLMClientError
            On auth failure, malformed responses, or transport/rate-limit
            failure persisting past ``max_retries``.
        """
        payload: Dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            # OpenRouter attribution headers (optional but recommended).
            "HTTP-Referer": "https://github.com/Kishorevijay07/AgentOS",
            "X-Title": "AgentOS",
        }

        last_error: Optional[str] = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = self._session.post(
                    self._url, json=payload, headers=headers, timeout=self._timeout
                )
            except requests.RequestException as exc:
                last_error = f"transport error: {exc}"
                logger.warning("OpenRouter attempt %d/%d failed: %s",
                               attempt, self._max_retries, last_error)
                self._backoff(attempt)
                continue

            if response.status_code == 200:
                return self._extract_text(response)

            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
            if response.status_code in _RETRYABLE_STATUSES:
                logger.warning("OpenRouter attempt %d/%d retryable failure: %s",
                               attempt, self._max_retries, last_error)
                self._backoff(attempt)
                continue

            # Non-retryable (401/403/400/…): fail fast with a clear message.
            raise LLMClientError(f"OpenRouter request failed: {last_error}")

        raise LLMClientError(
            f"OpenRouter request failed after {self._max_retries} attempts: {last_error}"
        )

    # ------------------------------------------------------------------ #
    #  Internals
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_text(response: requests.Response) -> str:
        """Pull the completion text out of an OpenAI-shaped response body."""
        try:
            data = response.json()
            choices: List[Dict[str, Any]] = data["choices"]
            content = choices[0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMClientError(
                f"OpenRouter returned an unexpected response shape: {exc}"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMClientError("OpenRouter returned an empty completion.")
        return content

    def _backoff(self, attempt: int) -> None:
        if attempt < self._max_retries:
            time.sleep(min(2 ** (attempt - 1), 8) * 0.5)  # 0.5s, 1s, 2s, … cap 4s

    def __repr__(self) -> str:  # pragma: no cover - cosmetic (never print the key)
        return f"OpenRouterLLMClient(model={self._model!r})"
