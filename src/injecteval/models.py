"""Model backends.

* ``OpenAICompatibleModel`` talks to any server that implements the OpenAI chat-completions API with
  tool calling - Ollama, vLLM, llama.cpp server, LM Studio, or a hosted endpoint.
* ``MockModel`` is a deterministic stand-in used by the test suite and for trying the harness without
  a model. It does not measure anything: it only replays the calls written in the scenario file.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from .scenario import Scenario


@dataclass
class ModelResponse:
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)  # [{"id", "name", "arguments"}]


class Model(Protocol):
    name: str

    def complete(self, messages: list[dict[str, Any]], tools: list[dict]) -> ModelResponse: ...


class ModelError(RuntimeError):
    """The model backend could not produce a response."""


class OpenAICompatibleModel:
    def __init__(self, model: str, base_url: str, api_key: str | None = None, temperature: float = 0.0,
                 timeout: float = 120.0, client: httpx.Client | None = None):
        self.name = model
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = client or httpx.Client(timeout=timeout, headers=headers)

    def complete(self, messages: list[dict[str, Any]], tools: list[dict]) -> ModelResponse:
        payload = {"model": self.model, "messages": messages, "tools": tools, "temperature": self.temperature}
        try:
            response = self._client.post(f"{self.base_url}/chat/completions", json=payload)
            response.raise_for_status()
            message = response.json()["choices"][0]["message"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as error:
            raise ModelError(f"request to {self.base_url} failed: {error}") from error

        tool_calls = []
        for index, call in enumerate(message.get("tool_calls") or []):
            function = call.get("function", {})
            tool_calls.append({
                "id": call.get("id") or f"call_{index}",
                "name": function.get("name", ""),
                "arguments": _parse_arguments(function.get("arguments")),
            })
        return ModelResponse(content=message.get("content") or "", tool_calls=tool_calls)


def _parse_arguments(raw: Any) -> dict[str, Any]:
    """Models return arguments as a JSON string (OpenAI) or an object (some local servers)."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


class MockModel:
    """Replays the scenario's reference calls; optionally 'falls for' the injection.

    ``vulnerable``: completes the task, and if the payload text reached it through a tool result it
    also performs the attacker's calls - the behaviour of an agent with no resistance at all.
    ``robust``: completes the task and ignores the payload.
    """

    def __init__(self, scenario: Scenario, vulnerable: bool):
        self.name = "mock:vulnerable" if vulnerable else "mock:robust"
        self.scenario = scenario
        self.vulnerable = vulnerable
        self._queue: list[dict[str, Any]] = list(scenario.utility.get("reference_calls", []))
        self._attack_done = False
        self._counter = 0

    def complete(self, messages: list[dict[str, Any]], tools: list[dict]) -> ModelResponse:
        if self.vulnerable and not self._attack_done and self._payload_visible(messages):
            self._attack_done = True
            self._queue = list((self.scenario.attack or {}).get("simulated_calls", [])) + self._queue
        if self._queue:
            call = self._queue.pop(0)
            self._counter += 1
            return ModelResponse(tool_calls=[{
                "id": f"mock_{self._counter}", "name": call["tool"], "arguments": dict(call.get("args", {})),
            }])
        if self._attack_done and (self.scenario.attack or {}).get("simulated_answer"):
            return ModelResponse(content=self.scenario.attack["simulated_answer"])
        return ModelResponse(content=self.scenario.utility.get("reference_answer", "Done."))

    def _payload_visible(self, messages: list[dict[str, Any]]) -> bool:
        payload = self.scenario.payload
        if not payload:
            return False
        # The payload counts as "seen" only if its text survived whatever the defense did to it.
        key_line = max(payload.splitlines(), key=len).strip()
        return any(m.get("role") == "tool" and key_line in str(m.get("content", "")) for m in messages)


PROVIDER_DEFAULTS = {
    "ollama": "http://localhost:11434/v1",
    "lmstudio": "http://localhost:1234/v1",
    "vllm": "http://localhost:8000/v1",
    "openai": "https://api.openai.com/v1",
}


def is_mock(spec: str) -> bool:
    return spec in ("mock:vulnerable", "mock:robust")


def build_model(spec: str, scenario: Scenario, base_url: str | None = None, temperature: float = 0.0) -> Model:
    """Create a model from a ``provider:model`` string, e.g. ``ollama:llama3.2`` or ``mock:vulnerable``."""
    if is_mock(spec):
        return MockModel(scenario, vulnerable=spec.endswith("vulnerable"))
    provider, _, model = spec.partition(":")
    if not model:
        raise ValueError(f"model must look like 'provider:model' (e.g. ollama:llama3.2), got '{spec}'")
    url = base_url or os.environ.get("INJECTEVAL_BASE_URL") or PROVIDER_DEFAULTS.get(provider)
    if url is None:
        raise ValueError(f"unknown provider '{provider}': pass --base-url or use one of {sorted(PROVIDER_DEFAULTS)}")
    return OpenAICompatibleModel(model, url, api_key=os.environ.get("INJECTEVAL_API_KEY"), temperature=temperature)
