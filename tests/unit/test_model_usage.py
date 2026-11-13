"""Cached input tokens are recorded apart from uncached input."""

from __future__ import annotations

from typing import Any

from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

from incidentops.agent.model import ModelTurn, ToolCall, turn_from_response
from incidentops.agent.model import Usage as TurnUsage


def _message(**usage: Any) -> Message:
    return Message(
        id="msg_1",
        type="message",
        role="assistant",
        model="provider-model",
        content=[
            TextBlock(type="text", text="checking"),
            ToolUseBlock(type="tool_use", id="t1", name="search_logs", input={}),
        ],
        stop_reason="tool_use",
        stop_sequence=None,
        usage=Usage(**usage),
    )


def test_cache_usage_is_kept_separately() -> None:
    turn = turn_from_response(
        _message(
            input_tokens=250,
            output_tokens=40,
            cache_read_input_tokens=3000,
            cache_creation_input_tokens=120,
        )
    )
    assert turn.usage == TurnUsage(250, 40, 3000, 120)


def test_missing_cache_usage_counts_as_zero() -> None:
    turn = turn_from_response(_message(input_tokens=5, output_tokens=1))
    assert (turn.usage.cache_read_input_tokens, turn.usage.cache_creation_input_tokens) == (0, 0)


def test_scripted_turns_default_to_no_cache_usage() -> None:
    assert ModelTurn(tool_calls=[ToolCall("x", "submit_diagnosis", {})]).usage == TurnUsage()
