"""Control-plane tables (architecture §11).

Every lifecycle table carries ``state`` and ``version`` columns so transitions
can be applied with compare-and-swap. Immutable payloads (alert events,
evidence, diagnoses, action envelopes, audit events) are insert-only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from incidentops.domain.state_machines import (
    ACTION_MACHINE,
    APPROVAL_MACHINE,
    INCIDENT_MACHINE,
    RUN_MACHINE,
    StateMachine,
)

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

ID = String(40)
HASH = String(71)  # "sha256:" + 64 hex characters


def _state_check(machine: StateMachine[Any], column: str = "state") -> CheckConstraint:
    allowed = ", ".join(f"'{s.value}'" for s in sorted(machine.states))
    return CheckConstraint(f"{column} IN ({allowed})", name=f"{column}_valid")


def _terminal_predicate(machine: StateMachine[Any], column: str = "state") -> str:
    terminal = ", ".join(f"'{s.value}'" for s in sorted(machine.terminal_states))
    return f"{column} NOT IN ({terminal})"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class IncidentRow(Base):
    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    dedup_key: Mapped[str] = mapped_column(String(256), nullable=False)
    service_id: Mapped[str] = mapped_column(String(63), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    escalation_reason: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()

    __table_args__ = (
        _state_check(INCIDENT_MACHINE),
        # FR-002: at most one *active* incident per dedup key.
        Index(
            "uq_incidents_active_dedup_key",
            "dedup_key",
            unique=True,
            postgresql_where=text(_terminal_predicate(INCIDENT_MACHINE)),
        ),
        Index("ix_incidents_service_state", "service_id", "state"),
    )


class AlertEventRow(Base):
    __tablename__ = "alert_events"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(256), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    payload_digest: Mapped[str] = mapped_column(HASH, nullable=False)
    received_at: Mapped[datetime] = _now()

    __table_args__ = (Index("ix_alert_events_incident", "incident_id", "received_at"),)


class InvestigationRunRow(Base):
    __tablename__ = "investigation_runs"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), nullable=False)
    incident_version: Mapped[int] = mapped_column(Integer, nullable=False)
    checkpoint_thread_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    budgets: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    model_id: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()

    __table_args__ = (_state_check(RUN_MACHINE),)


class AgentStepRow(Base):
    __tablename__ = "agent_steps"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("investigation_runs.id"), nullable=False)
    parent_step_id: Mapped[str | None] = mapped_column(ForeignKey("agent_steps.id"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trace_id: Mapped[str | None] = mapped_column(String(32))
    span_id: Mapped[str | None] = mapped_column(String(16))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False)


class EvidenceRow(Base):
    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), nullable=False)
    run_id: Mapped[str] = mapped_column(ForeignKey("investigation_runs.id"), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    query_ref: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    digest: Mapped[str] = mapped_column(HASH, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    __table_args__ = (
        CheckConstraint("window_end >= window_start", name="window_ordered"),
        CheckConstraint("octet_length(snapshot) <= 16384", name="snapshot_budget"),
    )


class DiagnosisRow(Base):
    __tablename__ = "diagnoses"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("investigation_runs.id"), nullable=False)
    root_cause_id: Mapped[str] = mapped_column(String(64), nullable=False)
    report: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    cited_evidence_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _now()


class ActionProposalRow(Base):
    __tablename__ = "action_proposals"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), nullable=False)
    diagnosis_id: Mapped[str | None] = mapped_column(ForeignKey("diagnoses.id"))
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(63), nullable=False)
    target_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    envelope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    action_hash: Mapped[str] = mapped_column(HASH, nullable=False, unique=True)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    reason_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()

    __table_args__ = (_state_check(ACTION_MACHINE),)


class ApprovalRequestRow(Base):
    __tablename__ = "approval_requests"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    action_id: Mapped[str] = mapped_column(ForeignKey("action_proposals.id"), nullable=False)
    action_hash: Mapped[str] = mapped_column(HASH, nullable=False)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()

    __table_args__ = (
        _state_check(APPROVAL_MACHINE),
        # One active (pending or approved) request per action.
        Index(
            "uq_approval_requests_active_action",
            "action_id",
            unique=True,
            postgresql_where=text("state IN ('PENDING', 'APPROVED')"),
        ),
    )


class ApprovalDecisionRow(Base):
    __tablename__ = "approval_decisions"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    approval_id: Mapped[str] = mapped_column(ForeignKey("approval_requests.id"), nullable=False)
    approver_id: Mapped[str] = mapped_column(String(128), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    action_hash: Mapped[str] = mapped_column(HASH, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = _now()


class ExternalOperationRow(Base):
    __tablename__ = "external_operations"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    action_id: Mapped[str] = mapped_column(ForeignKey("action_proposals.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_operation_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    receipt: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()

    __table_args__ = (
        # One external execution per logical action and provider.
        UniqueConstraint("action_id", "provider", name="uq_external_operations_action_provider"),
    )


class VerificationRow(Base):
    __tablename__ = "verifications"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    action_id: Mapped[str] = mapped_column(ForeignKey("action_proposals.id"), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _now()

    __table_args__ = (
        CheckConstraint(
            "outcome IN ('PASSED', 'VERIFICATION_FAILED', 'UNKNOWN')", name="outcome_valid"
        ),
    )


class AuditEventRow(Base):
    """Append-only; UPDATE and DELETE are rejected by a database trigger."""

    __tablename__ = "audit_events"

    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    id: Mapped[str] = mapped_column(ID, nullable=False, unique=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action_hash: Mapped[str | None] = mapped_column(HASH)
    decision: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(Text)
    policy_version: Mapped[str | None] = mapped_column(String(64))
    correlation_ids: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    details_digest: Mapped[str | None] = mapped_column(HASH)
    occurred_at: Mapped[datetime] = _now()

    __table_args__ = (Index("ix_audit_events_resource", "resource_type", "resource_id"),)


class OutboxJobRow(Base):
    __tablename__ = "outbox_jobs"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(40), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="PENDING")
    available_at: Mapped[datetime] = _now()
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()

    __table_args__ = (
        CheckConstraint("status IN ('PENDING', 'LEASED', 'DONE', 'DEAD')", name="status_valid"),
        Index("ix_outbox_jobs_claimable", "kind", "status", "available_at"),
    )


class PolicyVersionRow(Base):
    __tablename__ = "policy_versions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    config_digest: Mapped[str] = mapped_column(HASH, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = _now()

    __table_args__ = (
        Index(
            "uq_policy_versions_single_active",
            "active",
            unique=True,
            postgresql_where=text("active"),
        ),
    )
