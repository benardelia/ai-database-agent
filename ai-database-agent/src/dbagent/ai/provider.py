from abc import ABC, abstractmethod
from typing import Any

import httpx

from dbagent.config import settings


class LLMProviderError(Exception):
    """The LLM backend itself failed (timeout, connection refused, bad
    response) -- distinct from the model producing a bad *answer*. Phase 40
    (Error Recovery) lists LLM failure as something the agent must handle,
    not crash on."""


class LLMProvider(ABC):
    """Small abstraction so the agent isn't hard-wired to one LLM backend.
    Ollama is the primary/default provider; other providers can implement
    the same interface later without touching the agent loop."""

    @abstractmethod
    def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """Send a chat turn and return the assistant message dict
        (may include a 'tool_calls' key). Raises LLMProviderError if the
        backend itself fails."""
        raise NotImplementedError


class OllamaProvider(LLMProvider):
    def __init__(
        self,
        host: str,
        model: str,
        timeout: float = 180.0,
        temperature: float = 0.0,
        keep_alive: str = "30m",
    ):
        self._host = host.rstrip("/")
        self._model = model
        self._client = httpx.Client(timeout=timeout)
        self._temperature = temperature
        # Ollama unloads a model from memory after ~5 minutes idle by
        # default; the next request then pays a full reload, which can be
        # slow enough to blow past a normal per-call timeout on its own,
        # on top of whatever the actual generation takes. Ask it to keep
        # the model resident longer between requests.
        self._keep_alive = keep_alive

    def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": self._temperature},
            "keep_alive": self._keep_alive,
        }
        if tools:
            payload["tools"] = tools

        try:
            response = self._client.post(f"{self._host}/api/chat", json=payload)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LLMProviderError(
                f"Ollama did not respond within {self._client.timeout.read}s. "
                "It may be reloading the model after being idle, or the "
                "machine may be under load. Try again, or increase the "
                "OLLAMA_TIMEOUT_SECONDS setting."
            ) from exc
        except httpx.ConnectError as exc:
            raise LLMProviderError(
                f"Could not connect to Ollama at {self._host}. Is it running "
                "('brew services start ollama' / 'ollama serve')?"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise LLMProviderError(
                f"Ollama returned an error ({exc.response.status_code}): "
                f"{exc.response.text[:500]}"
            ) from exc

        return response.json()["message"]


def build_ollama_provider() -> "OllamaProvider":
    """Construct the primary OllamaProvider from settings. All the app's
    entry points (API, MCP server, scripts) go through this instead of
    each repeating the same settings-to-constructor wiring."""
    return OllamaProvider(
        host=settings.ollama_host,
        model=settings.ollama_model,
        timeout=settings.ollama_timeout_seconds,
        keep_alive=settings.ollama_keep_alive,
    )
