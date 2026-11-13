"""Provider thinking blocks are replayed unchanged; empty turns are not replayed."""

from __future__ import annotations

from anthropic.types import (
    Message,
    RedactedThinkingBlock,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    Usage,
)

from incidentops.agent.model import ModelTurn, ToolCall, turn_from_response


def _response(*content: object, output_tokens: int = 10) -> Message:
    return Message(
        id="msg_1",
        type="message",
        role="assistant",
        model="provider-model",
        content=list(content),
        stop_reason="tool_use",
        stop_sequence=None,
        usage=Usage(input_tokens=5, output_tokens=output_tokens),
    )


def test_thinking_blocks_round_trip_in_order_with_signature() -> None:
    turn = turn_from_response(
        _response(
            ThinkingBlock(type="thinking", thinking="check lag first", signature="sig-1"),
            RedactedThinkingBlock(type="redacted_thinking", data="opaque"),
            TextBlock(type="text", text="Checking the consumer."),
            ToolUseBlock(type="tool_use", id="t1", name="get_consumer_status", input={"w": "c"}),
        )
    )
    assert turn.text == "Checking the consumer."
    assert turn.tool_calls == [ToolCall("t1", "get_consumer_status", {"w": "c"})]
    assert turn.assistant_message() == {
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "check lag first", "signature": "sig-1"},
            {"type": "redacted_thinking", "data": "opaque"},
            {"type": "text", "text": "Checking the consumer."},
            {"type": "tool_use", "id": "t1", "name": "get_consumer_status", "input": {"w": "c"}},
        ],
    }
    assert turn.usage.input_tokens == 5


def test_empty_text_blocks_are_not_replayed() -> None:
    turn = turn_from_response(
        _response(
            ThinkingBlock(type="thinking", thinking="", signature="sig-2"),
            TextBlock(type="text", text=""),
            ToolUseBlock(type="tool_use", id="t2", name="search_logs", input={}),
        )
    )
    message = turn.assistant_message()
    assert message is not None
    assert [b["type"] for b in message["content"]] == ["thinking", "tool_use"]


def test_thinking_only_turn_is_not_replayed() -> None:
    # A turn cut off by max_tokens while thinking has no text and no tool call;
    # an assistant message with empty content would be rejected with a 400.
    turn = turn_from_response(
        _response(
            ThinkingBlock(type="thinking", thinking="long reasoning", signature="s"),
            output_tokens=2048,
        )
    )
    assert turn.text is None
    assert turn.tool_calls == []
    assert turn.assistant_message() is None


def test_empty_turn_is_not_replayed() -> None:
    assert ModelTurn().assistant_message() is None


def test_turns_without_provider_blocks_keep_the_neutral_format() -> None:
    turn = ModelTurn(tool_calls=[ToolCall("x", "submit_diagnosis", {})], text="done")
    assert turn.assistant_message() == {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "done"},
            {"type": "tool_use", "id": "x", "name": "submit_diagnosis", "input": {}},
        ],
    }
