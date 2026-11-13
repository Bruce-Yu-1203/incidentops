"""Provider-neutral model adapter (ADR-008).

Conversation messages use a small neutral block format (``text``,
``tool_use``, ``tool_result``) that maps directly onto the Anthropic Messages
API. Final answers are delivered through the ``submit_diagnosis`` tool so the
output is schema-constrained rather than free text.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from incidentops.evidence.redaction import redact, truncate_utf8

if TYPE_CHECKING:
    import anthropic

SUBMIT_DIAGNOSIS = "submit_diagnosis"
# Provider error bodies are size-bounded before they reach logs or trajectories.
MAX_PROVIDER_ERROR_BYTES = 2048

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: Any


@dataclass(frozen=True)
class Usage:
    # Uncached input only; cached input is billed differently and counted in
    # the two cache fields, so total input is the sum of all three.
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


THINKING_BLOCK_TYPES = frozenset({"thinking", "redacted_thinking"})


@dataclass(frozen=True)
class ModelTurn:
    tool_calls: list[ToolCall] = field(default_factory=list)
    text: str | None = None
    usage: Usage = field(default_factory=Usage)
    # The provider's content blocks in their original order, as request
    # params. Thinking blocks must be sent back unchanged on the next request.
    # Empty for turns that did not come from a provider (scripted, baseline).
    blocks: list[dict[str, Any]] = field(default_factory=list)
    # Provider stop reason (e.g. "tool_use", "max_tokens"); None for scripted turns.
    stop_reason: str | None = None

    def assistant_message(self) -> dict[str, Any] | None:
        """The turn as an assistant message for the next request.

        ``None`` when the turn has no text and no tool call (e.g. a thinking-only
        turn cut off by ``max_tokens``): providers reject assistant messages
        with empty content, so such a turn is not replayed.
        """
        if not self.text and not self.tool_calls:
            return None
        if self.blocks:
            content = [b for b in self.blocks if not (b["type"] == "text" and not b["text"])]
        else:
            content = []
            if self.text:
                content.append({"type": "text", "text": self.text})
            content.extend(
                {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                for c in self.tool_calls
            )
        return {"role": "assistant", "content": content}


def block_param(block: Any) -> dict[str, Any] | None:
    """A response content block as a request param, or ``None`` if not replayed."""
    if block.type == "thinking":
        return {"type": "thinking", "thinking": block.thinking, "signature": block.signature}
    if block.type == "redacted_thinking":
        return {"type": "redacted_thinking", "data": block.data}
    if block.type == "text":
        return {"type": "text", "text": block.text}
    if block.type == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    return None


class ModelError(RuntimeError):
    """The provider failed or returned an unusable response."""


def describe_api_error(exc: anthropic.APIError) -> str:
    """A redacted, size-bounded description of a provider error.

    Keeps the status code and the provider's response body so a rejected
    request (e.g. a 400) can be diagnosed; secrets are redacted.
    """
    parts = [type(exc).__name__]
    status = getattr(exc, "status_code", None)
    if status is not None:
        parts.append(f"status={status}")
    body = exc.body if exc.body is not None else exc.message
    if body is not None:
        text = body if isinstance(body, str) else json.dumps(body, sort_keys=True, default=str)
        parts.append(f"body={text}")
    bounded, truncated = truncate_utf8(redact(" ".join(parts)), MAX_PROVIDER_ERROR_BYTES)
    return bounded + (" [truncated]" if truncated else "")


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
        base_url: str | None = None,
    ) -> None:
        import anthropic

        # base_url=None keeps the SDK default (api.anthropic.com).
        self._client = anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url, max_retries=2)
        self._model_id = model_id
        self._max_tokens = max_output_tokens
        self._timeout = timeout_seconds

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def max_output_tokens(self) -> int:
        return self._max_tokens

    @property
    def base_url(self) -> str:
        return str(self._client.base_url)

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
            detail = describe_api_error(exc)
            logger.warning("model call failed (model %s): %s", self._model_id, detail)
            raise ModelError(detail) from exc
        return turn_from_response(response)


def turn_from_response(response: anthropic.types.Message) -> ModelTurn:
    """Map a Messages API response onto a ModelTurn, keeping block order."""
    calls: list[ToolCall] = []
    texts: list[str] = []
    blocks: list[dict[str, Any]] = []
    for block in response.content:
        if block.type == "tool_use":
            calls.append(ToolCall(block.id, block.name, block.input))
        elif block.type == "text" and block.text:
            texts.append(block.text)
        param = block_param(block)
        if param is not None:
            blocks.append(param)
    return ModelTurn(
        tool_calls=calls,
        text="\n".join(texts) or None,
        blocks=blocks,
        stop_reason=response.stop_reason,
        usage=Usage(
            response.usage.input_tokens,
            response.usage.output_tokens,
            cache_read_input_tokens=response.usage.cache_read_input_tokens or 0,
            cache_creation_input_tokens=response.usage.cache_creation_input_tokens or 0,
        ),
    )
