"""Tests for secret and personal-data redaction (S-11)."""

from __future__ import annotations

import pytest

from incidentops.evidence.redaction import REDACTED, redact, truncate_utf8


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ("connect postgresql://lab:hunter2@lab-postgres:5432/lab failed", "hunter2"),
        ("Authorization: Bearer abc.def-ghi_123", "abc.def-ghi_123"),
        ("password=s3cr3t user=lab", "s3cr3t"),
        ('{"api_key": "k-123456"}', "k-123456"),
        ("token: 0123456789abcdef", "0123456789abcdef"),
        ("aws AKIAABCDEFGHIJKLMNOP used", "AKIAABCDEFGHIJKLMNOP"),
        ("key sk-ant-REDACTEDREDACTED0123456789 leaked", "sk-ant-REDACTEDREDACTED0123456789"),
        ("user alice@example.com logged in", "alice@example.com"),
    ],
)
def test_secrets_are_removed(raw: str, secret: str) -> None:
    cleaned = redact(raw)
    assert secret not in cleaned
    assert REDACTED in cleaned


def test_context_is_preserved() -> None:
    assert redact("password=x user=lab") == f"password={REDACTED} user=lab"
    assert redact("postgresql://lab:pw@db/lab") == f"postgresql://lab:{REDACTED}@db/lab"


def test_ordinary_text_and_trace_ids_untouched() -> None:
    text = "GET /items/5 503 trace_id=4bf92f3577b34da6a3ce929d0e0e4736 took 5.0s"
    assert redact(text) == text


def test_truncate_respects_character_boundaries() -> None:
    text, cut = truncate_utf8("é" * 10, 5)
    assert cut
    assert text == "éé"
    assert truncate_utf8("abc", 10) == ("abc", False)
