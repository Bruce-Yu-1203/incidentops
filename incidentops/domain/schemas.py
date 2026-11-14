"""Pydantic contracts shared across the control plane (roadmap P0).

All models forbid unknown fields. Anything that originates from the model or
from an external system is validated against these contracts before it can
influence workflow state.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Final

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from incidentops.domain.state_machines import ApprovalState, IncidentState

MAX_SNAPSHOT_BYTES: Final = 16 * 1024  # ADR-012

# Identifier shapes. These patterns intentionally reject URLs, IP addresses,
# paths, whitespace and shell metacharacters, so a model-provided value can
# never smuggle a network destination or command into an action.
ResourceId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")]
TypeId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
RecordId = Annotated[str, StringConstraints(pattern=r"^[a-z]+_[0-9a-f]{32}$")]
ShortText = Annotated[str, StringConstraints(max_length=2000)]
EvidenceNote = Annotated[str, StringConstraints(min_length=1, max_length=280)]

# Action arguments are restricted to JSON scalars without floats so the
# canonical encoding used for hashing is unambiguous.
ArgumentValue = str | int | bool | None


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


AwareDatetime = Annotated[datetime, AfterValidator(_require_aware)]


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)


class Environment(StrEnum):
    FAULT_LAB = "fault-lab"
    STAGING = "staging"
    PRODUCTION = "production"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(StrEnum):
    FIRING = "firing"
    RESOLVED = "resolved"


class AlertEvent(_Contract):
    """One normalized alert delivery (repeated deliveries are all retained)."""

    id: RecordId
    source_event_id: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    fingerprint: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{8,64}$")]
    alert_name: TypeId
    status: AlertStatus
    service_id: ResourceId
    environment: Environment
    severity: Severity
    labels: dict[str, Annotated[str, StringConstraints(max_length=512)]]
    annotations: dict[str, ShortText] = Field(default_factory=dict)
    starts_at: AwareDatetime
    ends_at: AwareDatetime | None = None
    received_at: AwareDatetime
    payload_digest: Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]


class Incident(_Contract):
    id: RecordId
    dedup_key: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    service_id: ResourceId
    environment: Environment
    severity: Severity
    state: IncidentState
    version: Annotated[int, Field(ge=1)]
    escalation_reason: TypeId | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class EvidenceSource(StrEnum):
    PROMETHEUS = "prometheus"
    LOKI = "loki"
    TEMPO = "tempo"
    WORKER_STATUS = "worker_status"
    DEPLOYMENT_CONTROLLER = "deployment_controller"


class QueryReference(_Contract):
    template_id: TypeId
    parameters: dict[str, ArgumentValue] = Field(default_factory=dict)


class Evidence(_Contract):
    """A measured fact with provenance (architecture §8)."""

    id: RecordId
    incident_id: RecordId
    run_id: RecordId
    source: EvidenceSource
    query: QueryReference
    window_start: AwareDatetime
    window_end: AwareDatetime
    observed_at: AwareDatetime
    digest: Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
    summary: ShortText
    snapshot: Annotated[str, StringConstraints(max_length=MAX_SNAPSHOT_BYTES)]
    truncated: bool = False

    def model_post_init(self, _context: object) -> None:
        if self.window_end < self.window_start:
            raise ValueError("evidence window_end precedes window_start")
        if len(self.snapshot.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
            raise ValueError("evidence snapshot exceeds the byte budget")


class ConfidenceLevel(StrEnum):
    """Categorical confidence; not a calibrated probability."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ActionProposalDraft(_Contract):
    """What the agent may propose. It is untrusted intent, never a command."""

    # Descriptions are part of the submit_diagnosis tool schema the model reads
    # (ADR-028); they state where each field lives because models misplaced them.
    action_type: TypeId = Field(
        description="An action listed for target_id in the service catalog and for "
        "root_cause_id in the root-cause action contract."
    )
    environment: Environment = Field(description="The target's environment, e.g. fault-lab.")
    target_id: ResourceId = Field(
        description="The service the action changes (it may differ from affected_component)."
    )
    arguments: dict[TypeId, ArgumentValue] = Field(
        default_factory=dict,
        description="Exactly the argument names of the action's `action_arguments` contract, "
        "each set to one plain value from its `one_of` list; {} only when the contract lists "
        "no arguments.",
    )
    reason: ShortText = Field(description="One or two sentences on why this action fixes it.")
    evidence_ids: list[RecordId] = Field(
        min_length=1, description="Evidence IDs showing the condition this action fixes."
    )


class Diagnosis(_Contract):
    root_cause_id: TypeId = Field(
        description="One of the allowed root causes; insufficient_evidence when no causal "
        "signal is confirmed."
    )
    affected_component: ResourceId = Field(
        description="The registered service that is failing (where the mechanism is), not the "
        "object an action would change, e.g. lab-api rather than its configuration."
    )
    confidence_level: ConfidenceLevel = Field(
        description="high: direct mechanism evidence on the component, consistent onset and a "
        "look-alike check, from at least two tools including a non-symptom check; medium: "
        "component clear, mechanism indirect or no look-alike check; low: symptoms only or "
        "conflicting evidence (use insufficient_evidence and propose nothing)."
    )
    summary: ShortText = Field(description="Plain-language explanation of the cause.")
    supporting_evidence_ids: list[RecordId] = Field(
        description="Evidence IDs returned by your tool calls that support the diagnosis."
    )
    contradicting_evidence_ids: list[RecordId] = Field(
        default_factory=list, description="Evidence IDs that argue against it, if any."
    )
    unresolved_questions: list[ShortText] = Field(
        default_factory=list, description="Open questions for the human reviewer."
    )
    # One plain-language sentence per cited evidence ID, for the console.
    # Optional: the UI falls back to the stored evidence summary.
    evidence_notes: dict[RecordId, EvidenceNote] = Field(
        default_factory=dict,
        description="Map from each cited evidence ID to one short sentence on what it shows; "
        "only IDs cited in this report.",
    )


class DiagnosisReport(_Contract):
    """Terminal output of an investigation run (architecture §6.4)."""

    incident_id: RecordId
    diagnosis: Diagnosis = Field(
        description="The diagnosis, as a JSON object (never a JSON-encoded string). It does "
        "not contain proposed_action or needs_human_review: those are top-level fields."
    )
    proposed_action: ActionProposalDraft | None = Field(
        default=None,
        description="Top-level field next to diagnosis: the action as a JSON object, or null "
        "when no action is proposed.",
    )
    needs_human_review: bool = Field(
        default=False,
        description="Top-level field next to diagnosis (not inside it); true when "
        "proposed_action is null.",
    )

    def model_post_init(self, _context: object) -> None:
        if self.proposed_action is None and not self.needs_human_review:
            # No-action reports must explicitly hand control back to a human.
            raise ValueError("a report without a proposed action must set needs_human_review")
        unknown = set(self.diagnosis.evidence_notes) - self.cited_evidence_ids()
        if unknown:
            raise ValueError("evidence_notes may only describe cited evidence IDs")

    def cited_evidence_ids(self) -> set[str]:
        """All evidence IDs the report relies on; each must resolve in the store."""
        cited = set(self.diagnosis.supporting_evidence_ids)
        cited.update(self.diagnosis.contradicting_evidence_ids)
        if self.proposed_action is not None:
            cited.update(self.proposed_action.evidence_ids)
        return cited


class Approval(_Contract):
    id: RecordId
    action_id: RecordId
    action_hash: Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
    state: ApprovalState
    requested_by: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    approver_id: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None
    reason: ShortText | None = None
    policy_version: TypeId
    expires_at: AwareDatetime
    decided_at: AwareDatetime | None = None


class AuditEvent(_Contract):
    """Append-only security-relevant event emitted by trusted code paths."""

    id: RecordId
    seq: Annotated[int, Field(ge=1)] | None = None
    actor: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    event_type: TypeId
    resource_type: TypeId
    resource_id: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    action_hash: Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")] | None = None
    decision: TypeId | None = None
    reason: ShortText | None = None
    policy_version: TypeId | None = None
    correlation_ids: dict[TypeId, str] = Field(default_factory=dict)
    occurred_at: AwareDatetime


class ReceiptStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class ActionReceipt(_Contract):
    """External operation receipt keyed by the stable action ID."""

    action_id: RecordId
    provider: TypeId
    provider_operation_id: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    status: ReceiptStatus
    target_revision: Annotated[str, StringConstraints(max_length=128)] | None = None
    detail: ShortText | None = None
    received_at: AwareDatetime


class VerificationOutcome(StrEnum):
    """ADR-013: only PASSED may resolve an incident."""

    PASSED = "PASSED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    UNKNOWN = "UNKNOWN"


class VerificationCheck(_Contract):
    check_id: TypeId
    passed: bool | None
    observed: ShortText
    sample_count: Annotated[int, Field(ge=0)] = 0


class Verification(_Contract):
    id: RecordId
    action_id: RecordId
    outcome: VerificationOutcome
    checks: list[VerificationCheck] = Field(min_length=1)
    window_start: AwareDatetime
    window_end: AwareDatetime
    evidence_ids: list[RecordId] = Field(default_factory=list)

    def model_post_init(self, _context: object) -> None:
        results = [c.passed for c in self.checks]
        if self.outcome is VerificationOutcome.PASSED and not all(r is True for r in results):
            raise ValueError("PASSED verification requires every check to pass")
        if self.outcome is VerificationOutcome.VERIFICATION_FAILED and False not in results:
            raise ValueError("VERIFICATION_FAILED requires at least one failed check")
