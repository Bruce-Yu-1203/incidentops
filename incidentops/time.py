"""Clock helpers; one place to obtain timezone-aware UTC timestamps."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)
