from abc import ABC, abstractmethod
from typing import Any

import httpx


class LLMProvider(ABC):
    """Small abstraction so the agent isn't hard-wired to one LLM backend.
    Ollama is the primary/default provider; other providers can implement
    the same interface later without touching the agent loop."""

    @abstractmethod
    def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """Send a chat turn and return the assistant message dict
        (may include a 'tool_calls' key)."""
        raise NotImplementedError


class OllamaProvider(LLMProvider):
    def __init__(
        self, host: str, model: str, timeout: float = 120.0, temperature: float = 0.0
    ):
        self._host = host.rstrip("/")
        self._model = model
        self._client = httpx.Client(timeout=timeout)
        self._temperature = temperature

    def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": self._temperature},
        }
        if tools:
            payload["tools"] = tools

        response = self._client.post(f"{self._host}/api/chat", json=payload)
        response.raise_for_status()
        return response.json()["message"]
