"""Turns an agent's proposed action into an immutable, policy-checked envelope.

The agent's draft is untrusted intent. This trusted step reads the target's
current revision itself, evaluates deterministic policy, freezes the exact
envelope with its ``action_hash`` and either records the denial or opens a
human approval request (architecture §9).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from incidentops.actions.targets import TargetInspector
from incidentops.domain.actions import ActionEnvelope
from incidentops.domain.ids import new_id
from incidentops.domain.schemas import DiagnosisReport
from incidentops.domain.state_machines import ActionState, ApprovalState, IncidentState
from incidentops.persistence.models import (
    ActionProposalRow,
    ApprovalRequestRow,
    AuditEventRow,
    DiagnosisRow,
    IncidentRow,
)
from incidentops.persistence.transitions import AuditSpec, transition
from incidentops.policy.engine import Decision, PolicyEngine, PolicyRequest, ReasonCode
from incidentops.time import utcnow

ACTOR: Final = "service:proposal-evaluator"
UNKNOWN_REVISION: Final = "unknown"
ACTIVE_ACTION_STATES: Final = frozenset(
    {
        ActionState.PENDING_APPROVAL,
        ActionState.APPROVED,
        ActionState.QUEUED,
        ActionState.EXECUTING,
        ActionState.RECONCILING,
    }
)


@dataclass(frozen=True)
class EvaluationOutcome:
    action_id: str | None
    decision: Decision | None
    reason: str


class ProposalEvaluator:
    def __init__(
        self,
        factory: sessionmaker[Session],
        policy: PolicyEngine,
        inspector: TargetInspector,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._factory = factory
        self._policy = policy
        self._inspector = inspector
        self._clock = clock

    def _has_active_action(self, target_id: str) -> bool:
        active = [s.value for s in ACTIVE_ACTION_STATES]
        with self._factory() as session:
            return (
                session.scalars(
                    select(ActionProposalRow.id).where(
                        ActionProposalRow.target_id == target_id,
                        ActionProposalRow.state.in_(active),
                    )
                ).first()
                is not None
            )

    async def evaluate(self, incident_id: str, diagnosis_id: str) -> EvaluationOutcome:
        with self._factory() as session:
            diagnosis = session.get(DiagnosisRow, diagnosis_id)
            incident = session.get(IncidentRow, incident_id)
            existing = session.scalars(
                select(ActionProposalRow.id).where(ActionProposalRow.diagnosis_id == diagnosis_id)
            ).first()
        if existing is not None:
            return EvaluationOutcome(existing, None, "already_evaluated")
        if diagnosis is None or incident is None or incident.state != IncidentState.DIAGNOSED:
            return EvaluationOutcome(None, None, "not_awaiting_evaluation")
        report = DiagnosisReport.model_validate(diagnosis.report)
        draft = report.proposed_action
        if draft is None:
            return EvaluationOutcome(None, None, "no_proposal")

        target = await self._inspector.inspect(draft.target_id)
        revision = str(target["revision"]) if target else None
        decision = self._policy.evaluate(
            PolicyRequest(
                principal=ACTOR,
                action_type=draft.action_type,
                environment=draft.environment.value,
                target_id=draft.target_id,
                arguments=dict(draft.arguments),
                target_revision=revision,
                root_cause_id=report.diagnosis.root_cause_id,
            )
        )
        if decision.decision is Decision.REQUIRES_APPROVAL and self._has_active_action(
            draft.target_id
        ):
            # One fault often opens incidents on several services (e.g. Redis and
            # the API that uses it). Only one in-flight action per target.
            decision = decision.model_copy(
                update={
                    "decision": Decision.DENY,
                    "reason_code": ReasonCode.DUPLICATE_ACTIVE_ACTION,
                }
            )
        now = self._clock()
        envelope = ActionEnvelope(
            action_id=new_id("act"),
            action_type=draft.action_type,
            environment=draft.environment,
            target_id=draft.target_id,
            target_revision=revision or UNKNOWN_REVISION,
            arguments=dict(draft.arguments),
            requested_by=f"run:{diagnosis.run_id}",
            evidence_ids=tuple(draft.evidence_ids),
            policy_version=decision.policy_version,
            expires_at=now + timedelta(seconds=self._policy.config.approval_ttl_seconds),
        )

        with self._factory.begin() as session:
            incident = session.get_one(IncidentRow, incident_id, with_for_update=True)
            if incident.state != IncidentState.DIAGNOSED:
                return EvaluationOutcome(None, None, "not_awaiting_evaluation")
            session.add(
                ActionProposalRow(
                    id=envelope.action_id,
                    incident_id=incident_id,
                    diagnosis_id=diagnosis_id,
                    action_type=envelope.action_type,
                    environment=envelope.environment.value,
                    target_id=envelope.target_id,
                    target_revision=envelope.target_revision,
                    envelope=envelope.model_dump(mode="json"),
                    action_hash=envelope.action_hash,
                    policy_version=envelope.policy_version,
                    expires_at=envelope.expires_at,
                    state=ActionState.PROPOSED.value,
                    version=1,
                )
            )
            session.add(
                AuditEventRow(
                    id=new_id("aud"),
                    actor=ACTOR,
                    event_type="policy_evaluated",
                    resource_type="action_proposals",
                    resource_id=envelope.action_id,
                    action_hash=envelope.action_hash,
                    decision=decision.decision.value,
                    reason=decision.reason_code.value,
                    policy_version=decision.policy_version,
                    correlation_ids={"incident_id": incident_id, "diagnosis_id": diagnosis_id},
                )
            )
            session.flush()
            audit = AuditSpec(
                actor=ACTOR,
                event_type="action_state_changed",
                reason=decision.reason_code.value,
                action_hash=envelope.action_hash,
                policy_version=decision.policy_version,
                correlation_ids={"incident_id": incident_id},
            )
            if decision.decision is not Decision.REQUIRES_APPROVAL:
                # ALLOW is not reachable for writes (policy schema), so anything
                # other than REQUIRES_APPROVAL is a denial.
                transition(
                    session,
                    ActionProposalRow,
                    envelope.action_id,
                    expected_state=ActionState.PROPOSED,
                    expected_version=1,
                    target_state=ActionState.POLICY_DENIED,
                    audit=audit,
                    extra_values={"reason_code": decision.reason_code.value},
                )
                transition(
                    session,
                    IncidentRow,
                    incident_id,
                    expected_state=IncidentState.DIAGNOSED,
                    expected_version=incident.version,
                    target_state=IncidentState.DIAGNOSED_ONLY,
                    audit=AuditSpec(
                        actor=ACTOR,
                        event_type="proposal_denied",
                        reason=decision.reason_code.value,
                        correlation_ids={"action_id": envelope.action_id},
                    ),
                    extra_values={"escalation_reason": f"policy_{decision.reason_code.value}"},
                )
                return EvaluationOutcome(envelope.action_id, decision.decision, "policy_denied")

            transition(
                session,
                ActionProposalRow,
                envelope.action_id,
                expected_state=ActionState.PROPOSED,
                expected_version=1,
                target_state=ActionState.PENDING_APPROVAL,
                audit=audit,
            )
            session.add(
                ApprovalRequestRow(
                    id=new_id("apr"),
                    action_id=envelope.action_id,
                    action_hash=envelope.action_hash,
                    requested_by=envelope.requested_by,
                    policy_version=envelope.policy_version,
                    expires_at=envelope.expires_at,
                    state=ApprovalState.PENDING.value,
                    version=1,
                )
            )
            transition(
                session,
                IncidentRow,
                incident_id,
                expected_state=IncidentState.DIAGNOSED,
                expected_version=incident.version,
                target_state=IncidentState.AWAITING_APPROVAL,
                audit=AuditSpec(
                    actor=ACTOR,
                    event_type="approval_requested",
                    action_hash=envelope.action_hash,
                    correlation_ids={"action_id": envelope.action_id},
                ),
            )
            return EvaluationOutcome(envelope.action_id, decision.decision, "approval_requested")
