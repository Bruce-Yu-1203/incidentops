"""Evidence preconditions for lock and connection releases (ADR-027)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from incidentops.domain.catalog import load_catalog
from incidentops.policy.engine import (
    Decision,
    PolicyEngine,
    PolicyRequest,
    PolicyRule,
    ReasonCode,
    load_policy,
)
from incidentops.policy.evidence_facts import (
    POSTGRES_CONNECTIONS_NEAR_LIMIT,
    POSTGRES_EXCLUSIVE_LOCK_PRESENT,
    CitedEvidence,
    facts_for_target,
)

PG = "lab-postgres"


def _range(last: float, labels: dict[str, str] | None = None, peak: float | None = None) -> str:
    return json.dumps(
        [{"labels": labels or {}, "first": 0.0, "last": last, "max": max(last, peak or 0.0)}]
    )


def _health(exclusive: float, share: float | None) -> str:
    return json.dumps(
        {
            "postgres_locks_by_mode": [
                {"labels": {"mode": "accessexclusivelock"}, "value": exclusive},
                {"labels": {"mode": "accesssharelock"}, "value": 50.0},
            ],
            "connection_share": share,
        }
    )


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        (
            CitedEvidence("postgres_exclusive_locks", PG, _range(1.0)),
            {POSTGRES_EXCLUSIVE_LOCK_PRESENT},
        ),
        (CitedEvidence("postgres_exclusive_locks", PG, _range(0.0)), set()),
        (
            CitedEvidence(
                "postgres_locks_by_mode", PG, _range(2.0, {"mode": "accessexclusivelock"})
            ),
            {POSTGRES_EXCLUSIVE_LOCK_PRESENT},
        ),
        (
            CitedEvidence("postgres_locks_by_mode", PG, _range(50.0, {"mode": "accesssharelock"})),
            set(),
        ),
        (
            CitedEvidence("postgres_health", PG, _health(1.0, 0.65)),
            {POSTGRES_EXCLUSIVE_LOCK_PRESENT},
        ),
        (
            CitedEvidence("postgres_health", PG, _health(0.0, 0.94)),
            {POSTGRES_CONNECTIONS_NEAR_LIMIT},
        ),
        (CitedEvidence("postgres_health", PG, _health(0.0, 0.79)), set()),
        (
            CitedEvidence("postgres_connection_utilization", PG, _range(0.8)),
            {POSTGRES_CONNECTIONS_NEAR_LIMIT},
        ),
        (CitedEvidence("postgres_connection_utilization", PG, _range(0.65)), set()),
        # An earlier peak in the window that is gone now is not a current lock
        # (2026-11-13: a previous trial's lock was cited in B-F07/B-F13/B-N01).
        (CitedEvidence("postgres_exclusive_locks", PG, _range(0.0, peak=1.0)), set()),
        (CitedEvidence("postgres_connection_utilization", PG, _range(0.03, peak=0.94)), set()),
        # Symptom-only evidence never shows a database condition.
        (CitedEvidence("api_error_ratio", PG, _range(0.9)), set()),
        # Unparsable (e.g. truncated) snapshots fail closed.
        (CitedEvidence("postgres_exclusive_locks", PG, '[{"max": 1'), set()),
    ],
)
def test_facts_from_evidence(evidence: CitedEvidence, expected: set[str]) -> None:
    assert facts_for_target([evidence], PG) == expected


def test_evidence_about_another_service_is_ignored() -> None:
    other = CitedEvidence("postgres_exclusive_locks", "other-db", _range(1.0))
    assert facts_for_target([other], PG) == frozenset()


def _request(action: str, facts: frozenset[str] | None, root_cause: str) -> PolicyRequest:
    fields: dict[str, Any] = {
        "principal": "service:proposal-evaluator",
        "action_type": action,
        "environment": "fault-lab",
        "target_id": PG,
        "arguments": {},
        "target_revision": "pg-gen-1.lock-a.pool-b",
        "root_cause_id": root_cause,
        "confidence_level": "high",
        "evidence_facts": facts,
    }
    return PolicyRequest(**fields)


@pytest.mark.parametrize(
    ("action", "root_cause", "fact"),
    [
        ("release_lab_db_blocker", "postgres_lock_contention", POSTGRES_EXCLUSIVE_LOCK_PRESENT),
        (
            "release_lab_idle_connections",
            "connection_pool_exhausted",
            POSTGRES_CONNECTIONS_NEAR_LIMIT,
        ),
    ],
)
def test_release_needs_the_matching_evidence(action: str, root_cause: str, fact: str) -> None:
    engine = PolicyEngine(load_policy(), load_catalog())
    allowed = engine.evaluate(_request(action, frozenset({fact}), root_cause))
    assert allowed.decision is Decision.REQUIRES_APPROVAL
    for facts in (frozenset(), frozenset({"unrelated"})):
        denied = engine.evaluate(_request(action, facts, root_cause))
        assert (denied.decision, denied.reason_code) == (
            Decision.DENY,
            ReasonCode.REQUIRED_EVIDENCE_MISSING,
        )


def test_executor_recheck_without_facts_is_not_blocked() -> None:
    # The executor re-checks the frozen envelope; the diagnosis was already
    # evaluated with its evidence, so None skips the precondition.
    engine = PolicyEngine(load_policy(), load_catalog())
    request = _request("release_lab_db_blocker", None, "postgres_lock_contention")
    assert engine.evaluate(request).decision is Decision.REQUIRES_APPROVAL


def test_actions_without_preconditions_are_unaffected() -> None:
    engine = PolicyEngine(load_policy(), load_catalog())
    request = _request("restart_lab_dependency", frozenset(), "postgres_unavailable")
    assert engine.evaluate(request).decision is Decision.REQUIRES_APPROVAL


def test_unknown_evidence_facts_are_rejected_in_policy_config() -> None:
    with pytest.raises(ValidationError, match="unknown evidence facts"):
        PolicyRule(
            action_type="x_y",
            environments=("fault-lab",),
            decision=Decision.REQUIRES_APPROVAL,
            risk_class="low",
            required_evidence=("telepathy",),
        )
