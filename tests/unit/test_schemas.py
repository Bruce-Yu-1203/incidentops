"""Validation tests for shared domain contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from incidentops.domain.ids import new_id
from incidentops.domain.schemas import (
    MAX_SNAPSHOT_BYTES,
    ActionProposalDraft,
    ActionReceipt,
    AlertEvent,
    Approval,
    AuditEvent,
    Diagnosis,
    DiagnosisReport,
    Evidence,
    Incident,
    Verification,
    VerificationCheck,
    VerificationOutcome,
)
from incidentops.domain.state_machines import ApprovalState, IncidentState

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64


def _alert(**overrides: Any) -> AlertEvent:
    fields: dict[str, Any] = {
        "id": new_id("alert"),
        "source_event_id": "am-123",
        "fingerprint": "0123abcd0123abcd",
        "alert_name": "lab_kafka_consumer_lag_high",
        "status": "firing",
        "service_id": "persist-demo-consumer",
        "environment": "fault-lab",
        "severity": "warning",
        "labels": {"component": "kafka-consumer"},
        "starts_at": NOW,
        "received_at": NOW,
        "payload_digest": DIGEST,
    }
    fields.update(overrides)
    return AlertEvent(**fields)


def _evidence(**overrides: Any) -> Evidence:
    fields: dict[str, Any] = {
        "id": new_id("ev"),
        "incident_id": new_id("inc"),
        "run_id": new_id("run"),
        "source": "prometheus",
        "query": {"template_id": "kafka_group_lag", "parameters": {"window_seconds": 180}},
        "window_start": NOW - timedelta(minutes=3),
        "window_end": NOW,
        "observed_at": NOW,
        "digest": DIGEST,
        "summary": "Lag increasing",
        "snapshot": "[]",
    }
    fields.update(overrides)
    return Evidence(**fields)


def _diagnosis(**overrides: Any) -> Diagnosis:
    fields: dict[str, Any] = {
        "root_cause_id": "consumer_paused",
        "affected_component": "persist-demo-consumer",
        "confidence_level": "high",
        "summary": "Consumer paused; broker healthy.",
        "supporting_evidence_ids": [new_id("ev")],
    }
    fields.update(overrides)
    return Diagnosis(**fields)


def test_alert_event_accepts_valid_payload() -> None:
    alert = _alert()
    assert alert.status.value == "firing"
    assert alert.starts_at.tzinfo is UTC


def test_alert_event_normalizes_offsets_to_utc() -> None:
    plus_two = NOW.astimezone(timezone(timedelta(hours=2)))
    assert _alert(starts_at=plus_two).starts_at == NOW


@pytest.mark.parametrize(
    "overrides",
    [
        {"fingerprint": "not-hex!"},
        {"environment": "mars"},
        {"payload_digest": "md5:abc"},
        {"starts_at": datetime(2026, 1, 1)},
        {"unexpected": "field"},
    ],
)
def test_alert_event_rejects_invalid_fields(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _alert(**overrides)


def test_incident_requires_positive_version() -> None:
    with pytest.raises(ValidationError):
        Incident(
            id=new_id("inc"),
            dedup_key="k",
            service_id="lab-api",
            environment="fault-lab",
            severity="warning",
            state=IncidentState.RECEIVED,
            version=0,
            created_at=NOW,
            updated_at=NOW,
        )


def test_evidence_rejects_inverted_window() -> None:
    with pytest.raises(ValidationError, match="window_end"):
        _evidence(window_start=NOW, window_end=NOW - timedelta(seconds=1))


def test_evidence_rejects_oversized_snapshot() -> None:
    with pytest.raises(ValidationError):
        _evidence(snapshot="x" * (MAX_SNAPSHOT_BYTES + 1))


def test_evidence_counts_bytes_not_characters() -> None:
    # Each "é" is two UTF-8 bytes, so half the character budget overflows.
    with pytest.raises(ValidationError, match="byte budget"):
        _evidence(snapshot="é" * (MAX_SNAPSHOT_BYTES // 2 + 1))


def test_evidence_query_rejects_arbitrary_template_text() -> None:
    with pytest.raises(ValidationError):
        _evidence(query={"template_id": "sum(rate(x[5m]))"})


def test_report_without_action_requires_human_review() -> None:
    with pytest.raises(ValidationError, match="needs_human_review"):
        DiagnosisReport(incident_id=new_id("inc"), diagnosis=_diagnosis())
    report = DiagnosisReport(
        incident_id=new_id("inc"),
        diagnosis=_diagnosis(root_cause_id="insufficient_evidence", confidence_level="low"),
        needs_human_review=True,
    )
    assert report.proposed_action is None


def test_report_collects_all_cited_evidence() -> None:
    support, contra, action_ev = new_id("ev"), new_id("ev"), new_id("ev")
    report = DiagnosisReport(
        incident_id=new_id("inc"),
        diagnosis=_diagnosis(
            supporting_evidence_ids=[support], contradicting_evidence_ids=[contra]
        ),
        proposed_action=ActionProposalDraft(
            action_type="resume_test_consumer",
            environment="fault-lab",
            target_id="persist-demo-consumer",
            arguments={"desired_paused": False},
            reason="Restore consumption.",
            evidence_ids=[action_ev],
        ),
    )
    assert report.cited_evidence_ids() == {support, contra, action_ev}


def test_action_draft_requires_evidence_and_rejects_extra_fields() -> None:
    base: dict[str, Any] = {
        "action_type": "resume_test_consumer",
        "environment": "fault-lab",
        "target_id": "persist-demo-consumer",
        "reason": "r",
    }
    with pytest.raises(ValidationError):
        ActionProposalDraft(**base, evidence_ids=[])
    with pytest.raises(ValidationError):
        ActionProposalDraft(**base, evidence_ids=[new_id("ev")], command="ls")  # type: ignore[call-arg]


def test_confidence_is_categorical() -> None:
    with pytest.raises(ValidationError):
        _diagnosis(confidence_level="0.93")


def _check(passed: bool | None) -> VerificationCheck:
    return VerificationCheck(check_id="lag_declining", passed=passed, observed="x", sample_count=5)


@pytest.mark.parametrize(
    ("outcome", "checks", "valid"),
    [
        (VerificationOutcome.PASSED, [True, True], True),
        (VerificationOutcome.PASSED, [True, None], False),
        (VerificationOutcome.PASSED, [True, False], False),
        (VerificationOutcome.VERIFICATION_FAILED, [True, False], True),
        (VerificationOutcome.VERIFICATION_FAILED, [True, None], False),
        (VerificationOutcome.UNKNOWN, [None], True),
    ],
)
def test_verification_outcome_consistency(
    outcome: VerificationOutcome, checks: list[bool | None], valid: bool
) -> None:
    def build() -> Verification:
        return Verification(
            id=new_id("ver"),
            action_id=new_id("act"),
            outcome=outcome,
            checks=[_check(c) for c in checks],
            window_start=NOW,
            window_end=NOW + timedelta(minutes=1),
        )

    if valid:
        assert build().outcome is outcome
    else:
        with pytest.raises(ValidationError):
            build()


def test_approval_audit_and_receipt_contracts() -> None:
    action_id = new_id("act")
    approval = Approval(
        id=new_id("apr"),
        action_id=action_id,
        action_hash=DIGEST,
        state=ApprovalState.PENDING,
        requested_by="run_x",
        policy_version="p1",
        expires_at=NOW + timedelta(minutes=15),
    )
    audit = AuditEvent(
        id=new_id("aud"),
        actor="user:approver-1",
        event_type="approval_decided",
        resource_type="action",
        resource_id=action_id,
        action_hash=approval.action_hash,
        decision="approved",
        policy_version="p1",
        occurred_at=NOW,
    )
    receipt = ActionReceipt(
        action_id=action_id,
        provider="lab_consumer_controller",
        provider_operation_id=action_id,
        status="ACCEPTED",
        target_revision="gen-9",
        received_at=NOW,
    )
    assert audit.resource_id == receipt.action_id == approval.action_id


def test_evidence_notes_describe_only_cited_evidence() -> None:
    cited = new_id("ev")
    report = DiagnosisReport(
        incident_id=new_id("inc"),
        diagnosis=_diagnosis(
            supporting_evidence_ids=[cited],
            evidence_notes={cited: "The consumer stopped reading at 08:07."},
        ),
        needs_human_review=True,
    )
    assert report.diagnosis.evidence_notes[cited].startswith("The consumer")
    with pytest.raises(ValidationError, match="evidence_notes"):
        DiagnosisReport(
            incident_id=new_id("inc"),
            diagnosis=_diagnosis(
                supporting_evidence_ids=[cited], evidence_notes={new_id("ev"): "Invented."}
            ),
            needs_human_review=True,
        )


def test_evidence_notes_are_short() -> None:
    cited = new_id("ev")
    with pytest.raises(ValidationError):
        _diagnosis(supporting_evidence_ids=[cited], evidence_notes={cited: "x" * 281})
