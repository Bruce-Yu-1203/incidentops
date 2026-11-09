"""Tests for the immutable action envelope and its canonical hash."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from incidentops.domain.actions import HASHED_FIELDS, ActionEnvelope, canonical_json
from incidentops.domain.ids import new_id

ACTION_ID = "act_" + "0" * 32
EVIDENCE_ID = "ev_" + "1" * 32
EXPIRES = datetime(2026, 11, 9, 12, 15, tzinfo=UTC)


def _envelope(**overrides: Any) -> ActionEnvelope:
    fields: dict[str, Any] = {
        "action_id": ACTION_ID,
        "action_type": "resume_test_consumer",
        "environment": "fault-lab",
        "target_id": "persist-demo-consumer",
        "target_revision": "gen-8",
        "arguments": {"desired_paused": False},
        "requested_by": "run_" + "2" * 32,
        "evidence_ids": (EVIDENCE_ID,),
        "policy_version": "p1",
        "expires_at": EXPIRES,
    }
    fields.update(overrides)
    return ActionEnvelope(**fields)


def test_hash_is_stable_and_prefixed() -> None:
    first = _envelope().action_hash
    assert first == _envelope().action_hash
    assert first.startswith("sha256:")
    assert len(first) == len("sha256:") + 64


def test_hash_known_value_regression() -> None:
    # Pinning the value detects accidental changes to the canonical encoding,
    # which would silently invalidate every stored approval.
    assert _envelope().action_hash == (
        "sha256:f79f1c79db1597c09febe89fd09a0e165dfe433627dfdd056f3edd2875e417a6"
    )
    assert canonical_json(_envelope().hash_payload()) == (
        '{"action_id":"act_00000000000000000000000000000000",'
        '"action_type":"resume_test_consumer","arguments":{"desired_paused":false},'
        '"environment":"fault-lab","expires_at":"2026-11-09T12:15:00.000000Z",'
        '"policy_version":"p1","schema_version":1,"target_id":"persist-demo-consumer",'
        '"target_revision":"gen-8"}'
    )


def test_hash_ignores_argument_insertion_order() -> None:
    a = _envelope(arguments={"desired_paused": False, "note": "x"})
    b = _envelope(arguments={"note": "x", "desired_paused": False})
    assert a.action_hash == b.action_hash


def test_hash_normalizes_timezone_representation() -> None:
    eastern = timezone(timedelta(hours=-5))
    same_instant = EXPIRES.astimezone(eastern)
    assert _envelope(expires_at=same_instant).action_hash == _envelope().action_hash


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("action_id", "act_" + "f" * 32),
        ("action_type", "rollback_demo_api"),
        ("environment", "production"),
        ("target_id", "other-consumer"),
        ("target_revision", "gen-9"),
        ("arguments", {"desired_paused": True}),
        ("policy_version", "p2"),
        ("expires_at", EXPIRES + timedelta(seconds=1)),
    ],
)
def test_any_bound_field_change_alters_hash(field: str, value: Any) -> None:
    # S-04: altered arguments/target/revision must not match the approval.
    original = _envelope()
    tampered = _envelope(**{field: value})
    assert tampered.action_hash != original.action_hash
    assert not tampered.matches(original.action_hash)


def test_hashed_fields_cover_required_binding() -> None:
    required = {"action_type", "target_id", "environment", "arguments", "target_revision"}
    assert required <= set(HASHED_FIELDS)
    assert {"expires_at", "schema_version"} <= set(HASHED_FIELDS)
    assert set(_envelope().hash_payload()) == set(HASHED_FIELDS)


def test_envelope_is_immutable() -> None:
    envelope = _envelope()
    with pytest.raises(ValidationError):
        envelope.target_id = "other"  # type: ignore[misc]


def test_matches_accepts_original_hash() -> None:
    envelope = _envelope()
    assert envelope.matches(envelope.action_hash)


def test_float_arguments_are_rejected() -> None:
    with pytest.raises(ValidationError):
        _envelope(arguments={"ratio": 0.5})


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        _envelope(command="rm -rf /")


@pytest.mark.parametrize(
    "target",
    ["http://evil.example", "10.0.0.1", "../etc", "a b", "consumer;rm", "UPPER"],
)
def test_target_id_rejects_urls_ips_and_metacharacters(target: str) -> None:
    with pytest.raises(ValidationError):
        _envelope(target_id=target)


def test_naive_expiry_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _envelope(expires_at=datetime(2026, 11, 9, 12, 15))


def test_is_expired_boundaries() -> None:
    envelope = _envelope()
    assert not envelope.is_expired(EXPIRES - timedelta(microseconds=1))
    assert envelope.is_expired(EXPIRES)


def test_new_id_produces_valid_action_id() -> None:
    assert _envelope(action_id=new_id("act")).action_id.startswith("act_")
