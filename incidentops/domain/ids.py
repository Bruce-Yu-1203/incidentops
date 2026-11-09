"""Stable, prefixed identifiers for domain records."""

from __future__ import annotations

import re
import uuid
from typing import Final

PREFIXES: Final = frozenset(
    {"inc", "alert", "run", "step", "ev", "diag", "act", "apr", "dec", "op", "ver", "aud", "job"}
)
_ID_PATTERN: Final = re.compile(r"^(?P<prefix>[a-z]+)_[0-9a-f]{32}$")


def new_id(prefix: str) -> str:
    """Return a new random identifier such as ``inc_3f2a...``."""
    if prefix not in PREFIXES:
        raise ValueError(f"unknown id prefix: {prefix}")
    return f"{prefix}_{uuid.uuid4().hex}"


def id_prefix(value: str) -> str:
    """Return the prefix of a well-formed identifier or raise ``ValueError``."""
    match = _ID_PATTERN.match(value)
    if match is None or match.group("prefix") not in PREFIXES:
        raise ValueError(f"malformed identifier: {value!r}")
    return match.group("prefix")
