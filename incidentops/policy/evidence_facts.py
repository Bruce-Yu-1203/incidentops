"""Deterministic facts read from cited evidence, for policy preconditions (ADR-027).

A model can name any root cause; some actions are only plausible when the
telemetry it cites actually shows the condition they fix. This module reads
the stored evidence snapshots (never the model's prose) and derives a small,
closed set of facts about a target. Anything it cannot parse yields no fact,
so a precondition fails closed.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final

# Facts describe the target's current state: the latest sample of a range
# reading, or an instant reading.
# An ACCESS EXCLUSIVE lock is held or awaited on the target database.
POSTGRES_EXCLUSIVE_LOCK_PRESENT: Final = "postgres_exclusive_lock_present"
# The target database's server connections are at CONNECTIONS_NEAR_LIMIT of
# max_connections or more.
POSTGRES_CONNECTIONS_NEAR_LIMIT: Final = "postgres_connections_near_limit"
KNOWN_FACTS: Final = frozenset({POSTGRES_EXCLUSIVE_LOCK_PRESENT, POSTGRES_CONNECTIONS_NEAR_LIMIT})

CONNECTIONS_NEAR_LIMIT: Final = 0.8
_EXCLUSIVE_MODE: Final = "accessexclusivelock"


@dataclass(frozen=True)
class CitedEvidence:
    """The parts of one stored evidence item that facts are derived from."""

    template_id: str
    service_id: str | None
    snapshot: str


def _range_last(rows: Any) -> float | None:
    """Largest *latest* value across summarized range series (query_metrics snapshots).

    The latest sample, not the window maximum: a 30-minute window can still
    hold an earlier, already-resolved peak (seen in the 2026-11-13 run, where
    a previous trial's lock was cited as if it were current).
    """
    if not isinstance(rows, list):
        return None
    values = [r.get("last") for r in rows if isinstance(r, dict)]
    numbers = [float(v) for v in values if isinstance(v, int | float)]
    return max(numbers) if numbers else None


def _instant(readings: Any, name: str) -> list[dict[str, Any]]:
    rows = readings.get(name) if isinstance(readings, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _facts_of(item: CitedEvidence) -> set[str]:
    try:
        data = json.loads(item.snapshot)
    except ValueError:
        return set()
    facts: set[str] = set()
    if item.template_id == "postgres_exclusive_locks":
        peak = _range_last(data)
        if peak is not None and peak > 0:
            facts.add(POSTGRES_EXCLUSIVE_LOCK_PRESENT)
    elif item.template_id == "postgres_locks_by_mode":
        exclusive = (
            [
                r
                for r in data
                if isinstance(r, dict) and (r.get("labels") or {}).get("mode") == _EXCLUSIVE_MODE
            ]
            if isinstance(data, list)
            else []
        )
        peak = _range_last(exclusive)
        if peak is not None and peak > 0:
            facts.add(POSTGRES_EXCLUSIVE_LOCK_PRESENT)
    elif item.template_id == "postgres_connection_utilization":
        peak = _range_last(data)
        if peak is not None and peak >= CONNECTIONS_NEAR_LIMIT:
            facts.add(POSTGRES_CONNECTIONS_NEAR_LIMIT)
    elif item.template_id == "postgres_health" and isinstance(data, dict):
        for row in _instant(data, "postgres_locks_by_mode"):
            value = row.get("value")
            mode = (row.get("labels") or {}).get("mode")
            if mode == _EXCLUSIVE_MODE and isinstance(value, int | float) and value > 0:
                facts.add(POSTGRES_EXCLUSIVE_LOCK_PRESENT)
        share = data.get("connection_share")
        if isinstance(share, int | float) and share >= CONNECTIONS_NEAR_LIMIT:
            facts.add(POSTGRES_CONNECTIONS_NEAR_LIMIT)
    return facts


def facts_for_target(evidence: Iterable[CitedEvidence], target_id: str) -> frozenset[str]:
    """Facts shown by evidence about ``target_id`` (evidence on other services is ignored)."""
    facts: set[str] = set()
    for item in evidence:
        if item.service_id == target_id:
            facts |= _facts_of(item)
    return frozenset(facts)
