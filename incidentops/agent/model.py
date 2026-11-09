"""Provider-neutral model adapter (ADR-008).

Conversation messages use a small neutral block format (``text``,
``tool_use``, ``tool_result``) that maps directly onto the Anthropic Messages
API. Final answers are delivered through the ``submit_diagnosis`` tool so the
output is schema-constrained rather than free text.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

SUBMIT_DIAGNOSIS = "submit_diagnosis"


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: Any


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class ModelTurn:
    tool_calls: list[ToolCall] = field(default_factory=list)
    text: str | None = None
    usage: Usage = field(default_factory=Usage)

    def assistant_message(self) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        if self.text:
            content.append({"type": "text", "text": self.text})
        content.extend(
            {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
            for c in self.tool_calls
        )
        return {"role": "assistant", "content": content}


class ModelError(RuntimeError):
    """The provider failed or returned an unusable response."""


class ModelAdapter(Protocol):
    @property
    def model_id(self) -> str: ...

    async def next_turn(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelTurn: ...


ScriptStep = ModelTurn | Callable[[list[dict[str, Any]]], ModelTurn]


class ScriptedModelAdapter:
    """Deterministic test double that replays a fixed script of turns.

    A step may be a callable that inspects the conversation so far, e.g. to
    cite evidence IDs returned by earlier tool calls.
    """

    def __init__(self, steps: Sequence[ScriptStep], model_id: str = "scripted-test-model") -> None:
        self._steps = list(steps)
        self._model_id = model_id
        self.calls: list[list[dict[str, Any]]] = []
        self.systems: list[str] = []
        self.tool_names: list[list[str]] = []

    @property
    def model_id(self) -> str:
        return self._model_id

    async def next_turn(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelTurn:
        self.calls.append(json.loads(json.dumps(messages)))
        self.systems.append(system)
        self.tool_names.append([t["name"] for t in tools])
        if not self._steps:
            raise ModelError("script exhausted")
        step = self._steps.pop(0)
        return step(messages) if callable(step) else step


def evidence_ids_in(messages: list[dict[str, Any]]) -> list[str]:
    """Evidence IDs returned by successful tool results so far (test helper)."""
    found: list[str] = []
    for message in messages:
        for block in message.get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                body = json.loads(block["content"])
                if body.get("evidence_id"):
                    found.append(body["evidence_id"])
    return found


class AnthropicModelAdapter:
    """Anthropic Messages API adapter with bounded output and timeouts."""

    def __init__(
        self,
        api_key: str,
        model_id: str,
        max_output_tokens: int = 2048,
        timeout_seconds: float = 60.0,
    ) -> None:
        import anthropic

        self._client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=2)
        self._model_id = model_id
        self._max_tokens = max_output_tokens
        self._timeout = timeout_seconds

    @property
    def model_id(self) -> str:
        return self._model_id

    async def next_turn(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelTurn:  # pragma: no cover - requires a live provider
        import anthropic

        try:
            response = await self._client.messages.create(
                model=self._model_id,
                max_tokens=self._max_tokens,
                system=system,
                messages=messages,  # type: ignore[arg-type]
                tools=tools,  # type: ignore[arg-type]
                timeout=self._timeout,
            )
        except anthropic.APIError as exc:
            raise ModelError(type(exc).__name__) from exc
        calls: list[ToolCall] = []
        texts: list[str] = []
        for block in response.content:
            if block.type == "tool_use":
                calls.append(ToolCall(block.id, block.name, block.input))
            elif block.type == "text":
                texts.append(block.text)
        return ModelTurn(
            tool_calls=calls,
            text="\n".join(texts) or None,
            usage=Usage(response.usage.input_tokens, response.usage.output_tokens),
        )
