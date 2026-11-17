"""A chat model with tool calling, through Ollama's /api/chat."""

from __future__ import annotations

import json
from typing import Any, Protocol

import httpx

from s2_agents.settings import AgentSettings


class LlmError(Exception):
    pass


class ChatModel(Protocol):
    """Takes the conversation and the tool specs; returns the assistant's next message:
    {"role": "assistant", "content": str, "tool_calls": [{"function": {"name", "arguments"}}]}."""

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]: ...


class Ollama:
    def __init__(self, settings: AgentSettings, http: httpx.Client | None = None) -> None:
        self._model = settings.ollama_model
        self._temperature = settings.temperature
        self._http = http or httpx.Client(base_url=settings.ollama_url, timeout=settings.ollama_timeout_s)

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            response = self._http.post(
                "/api/chat",
                json={
                    "model": self._model,
                    "messages": messages,
                    "tools": tools,
                    "stream": False,
                    "options": {"temperature": self._temperature},
                },
            )
        except httpx.HTTPError as exc:
            raise LlmError(f"Ollama is not reachable: {type(exc).__name__}: {exc}") from exc
        if response.status_code != 200:
            raise LlmError(f"Ollama answered HTTP {response.status_code}: {response.text[:200]}")
        message = response.json().get("message") or {}
        for call in message.get("tool_calls") or []:
            arguments = call.get("function", {}).get("arguments")
            if isinstance(arguments, str):  # some models return a JSON string
                try:
                    call["function"]["arguments"] = json.loads(arguments)
                except json.JSONDecodeError:
                    call["function"]["arguments"] = {}
        return {
            "role": "assistant",
            "content": message.get("content", ""),
            "tool_calls": message.get("tool_calls") or [],
        }
