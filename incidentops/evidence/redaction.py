"""Redaction of secrets and personal data before storage or model use (NFR-007).

Redaction is best-effort pattern matching; it reduces but does not eliminate
exposure, so raw payloads are additionally size-bounded and never logged.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Final

REDACTED: Final = "[REDACTED]"

_PATTERNS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    # Credentials embedded in connection URLs: scheme://user:password@host
    (re.compile(r"(?P<prefix>[a-z][a-z0-9+.-]*://[^:/\s@]+:)[^@\s]+(?=@)", re.I), r"\g<prefix>"),
    # Authorization headers and bearer tokens.
    (re.compile(r"(?P<prefix>\bbearer\s+)[A-Za-z0-9._~+/=-]+", re.I), r"\g<prefix>"),
    # key=value or key: value pairs whose key looks secret.
    (
        re.compile(
            r"(?P<prefix>\b(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)"
            r"\b[\"']?\s*[=:]\s*[\"']?)[^\s\"',;}]+",
            re.I,
        ),
        r"\g<prefix>",
    ),
    # Cloud access key IDs.
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), ""),
    # Anthropic/OpenAI-style API keys.
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), ""),
    # Email addresses (personal data).
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), ""),
)


def redact(text: str) -> str:
    """Return ``text`` with known secret and personal-data patterns replaced."""
    result = text
    for pattern, prefix in _PATTERNS:
        result = pattern.sub(_replacement(prefix), result)
    return result


def _replacement(prefix: str) -> Callable[[re.Match[str]], str]:
    def replace(match: re.Match[str]) -> str:
        return match.expand(prefix) + REDACTED

    return replace


def truncate_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    """Cut ``text`` to at most ``max_bytes`` UTF-8 bytes without splitting a character."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True
