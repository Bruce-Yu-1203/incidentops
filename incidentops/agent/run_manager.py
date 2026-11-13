"""Run manager: turns an ``investigate_incident`` job into a bounded agent run.

Lifecycle ownership (architecture §3): the agent returns findings only; this
trusted harness applies incident and run transitions with compare-and-swap,
persists the diagnosis and schedules follow-up work through the outbox.

A re-investigation (ADR-023) runs the same graph on an incident that automation
already handed to a person. It records a new diagnosis but never moves the
incident: handover states stay terminal, and a proposed action is shown to the
person without being sent to policy.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final

from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from incidentops.agent.graph import Budgets, build_graph, initial_state
from incidentops.agent.model import ModelAdapter
from incidentops.agent.prompt import PROMPT_VERSION, initial_message
from incidentops.domain.catalog import ServiceCatalog
from incidentops.domain.disposition import Disposition, needs_human
from incidentops.domain.ids import new_id
from incidentops.domain.schemas import DiagnosisReport
from incidentops.domain.state_machines import IncidentState, RunState
from incidentops.evidence.redaction import redact, truncate_utf8
from incidentops.evidence.store import EvidenceSink
from incidentops.persistence.models import (
    AlertEventRow,
    DiagnosisRow,
    EvidenceRow,
    IncidentRow,
    InvestigationRunRow,
    OutboxJobRow,
)
from incidentops.persistence.transitions import AuditSpec, transition
from incidentops.time import utcnow
from incidentops.tools.gateway import ToolGateway
from incidentops.trajectory.recorder import TrajectorySink

ACTOR: Final = "service:investigation-worker"
EVALUATE_PROPOSAL_JOB: Final = "evaluate_action_proposal"
INSUFFICIENT: Final = "insufficient_evidence"
PRIOR_CONTEXT_BYTES: Final = 8192


@dataclass(frozen=True)
class StartedRun:
    run_id: str
    incident_version: int
    thread_id: str
    incident_summary: dict[str, Any]
    alerts: list[dict[str, Any]]
    prior: dict[str, Any] | None = None


@dataclass(frozen=True)
class RunOutcome:
    run_id: str | None
    run_state: RunState | None
    incident_state: IncidentState
    error_code: str | None = None


def _audit(event: str, reason: str | None = None, **ids: str) -> AuditSpec:
    return AuditSpec(actor=ACTOR, event_type=event, reason=reason, correlation_ids=ids)


class InvestigationRunner:
    def __init__(
        self,
        factory: sessionmaker[Session],
        catalog: ServiceCatalog,
        model: ModelAdapter,
        gateway: ToolGateway,
        evidence: EvidenceSink,
        trajectory: TrajectorySink,
        budgets: Budgets,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._factory = factory
        self._catalog = catalog
        self._model = model
        self._gateway = gateway
        self._evidence = evidence
        self._trajectory = trajectory
        self._budgets = budgets
        self._checkpointer = checkpointer
        self._clock_ms = clock_ms

    def _start(self, incident_id: str) -> StartedRun | None:
        """Move the incident to INVESTIGATING and open a RUNNING run, atomically."""
        with self._factory.begin() as session:
            incident = session.get(IncidentRow, incident_id, with_for_update=True)
            if incident is None:
                return None
            state = IncidentState(incident.state)
            version = incident.version
            if state is IncidentState.RECEIVED:
                version = transition(
                    session,
                    IncidentRow,
                    incident_id,
                    expected_state=state,
                    expected_version=version,
                    target_state=IncidentState.TRIAGED,
                    audit=_audit("incident_triaged"),
                )
                state = IncidentState.TRIAGED
            if state is IncidentState.TRIAGED:
                version = transition(
                    session,
                    IncidentRow,
                    incident_id,
                    expected_state=state,
                    expected_version=version,
                    target_state=IncidentState.INVESTIGATING,
                    audit=_audit("investigation_started"),
                )
                state = IncidentState.INVESTIGATING
            if state is not IncidentState.INVESTIGATING:
                return None  # cancelled, terminal or already past investigation

            # A RUNNING run left behind by a crashed worker is closed explicitly;
            # its committed evidence stays, but its result is never fabricated.
            for stale in session.scalars(
                select(InvestigationRunRow).where(
                    InvestigationRunRow.incident_id == incident_id,
                    InvestigationRunRow.kind == "investigation",
                    InvestigationRunRow.state.in_([RunState.QUEUED.value, RunState.RUNNING.value]),
                )
            ).all():
                transition(
                    session,
                    InvestigationRunRow,
                    stale.id,
                    expected_state=RunState(stale.state),
                    expected_version=stale.version,
                    target_state=RunState.FAILED,
                    audit=_audit("run_abandoned", "worker_lost", incident_id=incident_id),
                    extra_values={"error_code": "worker_lost", "finished_at": utcnow()},
                )

            run_id = new_id("run")
            thread_id = f"{run_id}:{uuid.uuid4().hex[:8]}"
            session.add(
                InvestigationRunRow(
                    id=run_id,
                    incident_id=incident_id,
                    incident_version=version,
                    checkpoint_thread_id=thread_id,
                    state=RunState.QUEUED.value,
                    version=1,
                    budgets={
                        "max_steps": self._budgets.max_steps,
                        "max_tool_calls": self._budgets.max_tool_calls,
                        "wall_clock_seconds": self._budgets.wall_clock_seconds,
                    },
                    model_id=self._model.model_id,
                    prompt_version=PROMPT_VERSION,
                )
            )
            session.flush()
            transition(
                session,
                InvestigationRunRow,
                run_id,
                expected_state=RunState.QUEUED,
                expected_version=1,
                target_state=RunState.RUNNING,
                audit=_audit("run_started", incident_id=incident_id),
                extra_values={"started_at": utcnow()},
            )
            summary = {
                "id": incident.id,
                "service_id": incident.service_id,
                "environment": incident.environment,
                "severity": incident.severity,
                "created_at": incident.created_at.isoformat(),
            }
            alerts = [
                {
                    k: row.payload.get(k)
                    for k in ("alert_name", "status", "labels", "annotations", "starts_at")
                }
                for row in session.scalars(
                    select(AlertEventRow)
                    .where(AlertEventRow.incident_id == incident_id)
                    .order_by(AlertEventRow.received_at)
                )
            ]
            return StartedRun(run_id, version, thread_id, summary, alerts)

    async def run(self, incident_id: str) -> RunOutcome:
        started = self._start(incident_id)
        if started is None:
            with self._factory() as session:
                row = session.get(IncidentRow, incident_id)
                current = IncidentState(row.state) if row else IncidentState.FAILED
            return RunOutcome(None, None, current, "not_investigable")
        final = await self._execute(incident_id, started)
        return self._finish(incident_id, started.run_id, final)

    async def _execute(self, incident_id: str, started: StartedRun) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if self._clock_ms is not None:
            kwargs["clock_ms"] = self._clock_ms
        graph = build_graph(
            self._model,
            self._gateway,
            self._evidence,
            self._trajectory,
            self._budgets,
            checkpointer=self._checkpointer,
            **kwargs,
        )
        message = initial_message(started.incident_summary, started.alerts, self._catalog)
        if started.prior is not None:
            message = with_prior_context(message, started.prior)
        initial = initial_state(
            incident_id,
            started.run_id,
            started.incident_version,
            message,
            self._budgets,
            now_ms=self._clock_ms() if self._clock_ms else None,
        )
        try:
            final: dict[str, Any] = await graph.ainvoke(
                initial,
                {"configurable": {"thread_id": started.thread_id}, "recursion_limit": 200},
            )
        except Exception as exc:  # harness failure: record it, never invent a result
            final = {**initial, "status": "FAILED", "error_code": f"harness_{type(exc).__name__}"}
        return final

    # -- re-investigation (ADR-023) -------------------------------------------------

    def _start_reinvestigation(
        self, incident_id: str, requested_by: str, reason: str | None
    ) -> StartedRun | None:
        with self._factory.begin() as session:
            incident = session.get(IncidentRow, incident_id, with_for_update=True)
            if incident is None:
                return None
            disposition = Disposition(incident.disposition) if incident.disposition else None
            if not needs_human(IncidentState(incident.state), disposition):
                return None
            cutoff = utcnow() - timedelta(seconds=self._budgets.wall_clock_seconds + 300)
            for stale in session.scalars(
                select(InvestigationRunRow).where(
                    InvestigationRunRow.incident_id == incident_id,
                    InvestigationRunRow.kind == "reinvestigation",
                    InvestigationRunRow.state.in_([RunState.QUEUED.value, RunState.RUNNING.value]),
                    InvestigationRunRow.created_at < cutoff,
                )
            ).all():
                transition(
                    session,
                    InvestigationRunRow,
                    stale.id,
                    expected_state=RunState(stale.state),
                    expected_version=stale.version,
                    target_state=RunState.FAILED,
                    audit=_audit("run_abandoned", "worker_lost", incident_id=incident_id),
                    extra_values={"error_code": "worker_lost", "finished_at": utcnow()},
                )
            runs = list(
                session.scalars(
                    select(InvestigationRunRow).where(
                        InvestigationRunRow.incident_id == incident_id
                    )
                )
            )
            if any(
                r.kind == "reinvestigation"
                and r.state in (RunState.QUEUED.value, RunState.RUNNING.value)
                for r in runs
            ):
                return None
            kinds = {r.id: r.kind for r in runs}
            diagnosis = session.scalars(
                select(DiagnosisRow)
                .where(DiagnosisRow.run_id.in_(list(kinds)))
                .order_by(DiagnosisRow.created_at.desc())
            ).first()
            evidence = session.scalars(
                select(EvidenceRow)
                .where(EvidenceRow.incident_id == incident_id)
                .order_by(EvidenceRow.observed_at.desc())
                .limit(40)
            ).all()
            run_id = new_id("run")
            thread_id = f"{run_id}:{uuid.uuid4().hex[:8]}"
            session.add(
                InvestigationRunRow(
                    id=run_id,
                    incident_id=incident_id,
                    incident_version=incident.version,
                    checkpoint_thread_id=thread_id,
                    state=RunState.QUEUED.value,
                    version=1,
                    kind="reinvestigation",
                    requested_by=requested_by[:128],
                    budgets={
                        "max_steps": self._budgets.max_steps,
                        "max_tool_calls": self._budgets.max_tool_calls,
                        "wall_clock_seconds": self._budgets.wall_clock_seconds,
                    },
                    model_id=self._model.model_id,
                    prompt_version=PROMPT_VERSION,
                )
            )
            session.flush()
            transition(
                session,
                InvestigationRunRow,
                run_id,
                expected_state=RunState.QUEUED,
                expected_version=1,
                target_state=RunState.RUNNING,
                audit=_audit(
                    "reinvestigation_started",
                    reason,
                    incident_id=incident_id,
                    requested_by=requested_by,
                ),
                extra_values={"started_at": utcnow()},
            )
            report = diagnosis.report.get("diagnosis", {}) if diagnosis else {}
            prior = {
                "state_left_by_automation": incident.state,
                "escalation_reason": incident.escalation_reason,
                "previous_diagnosis": {
                    k: report.get(k)
                    for k in (
                        "root_cause_id",
                        "confidence_level",
                        "summary",
                        "supporting_evidence_ids",
                    )
                }
                if diagnosis
                else None,
                "earlier_evidence": [
                    {
                        "id": e.id,
                        "source": e.source,
                        "observed_at": e.observed_at.isoformat(),
                        "summary": e.summary[:200],
                        "found_by": kinds.get(e.run_id, "investigation"),
                    }
                    for e in evidence
                ],
                "requested_by": requested_by,
                "unverified_hypothesis": reason,
            }
            summary = {
                "id": incident.id,
                "service_id": incident.service_id,
                "environment": incident.environment,
                "severity": incident.severity,
                "created_at": incident.created_at.isoformat(),
            }
            alerts = [
                {
                    k: row.payload.get(k)
                    for k in ("alert_name", "status", "labels", "annotations", "starts_at")
                }
                for row in session.scalars(
                    select(AlertEventRow)
                    .where(AlertEventRow.incident_id == incident_id)
                    .order_by(AlertEventRow.received_at)
                )
            ]
            return StartedRun(run_id, incident.version, thread_id, summary, alerts, prior)

    async def reinvestigate(
        self, incident_id: str, requested_by: str, reason: str | None = None
    ) -> RunOutcome:
        started = self._start_reinvestigation(incident_id, requested_by, reason)
        if started is None:
            with self._factory() as session:
                row = session.get(IncidentRow, incident_id)
                current = IncidentState(row.state) if row else IncidentState.FAILED
            return RunOutcome(None, None, current, "not_reinvestigable")
        final = await self._execute(incident_id, started)
        return self._finish_reinvestigation(incident_id, started.run_id, final)

    def _finish_reinvestigation(
        self, incident_id: str, run_id: str, final: dict[str, Any]
    ) -> RunOutcome:
        status = final["status"]
        usage = {
            "step_count": final["step_count"],
            "tool_call_count": final["tool_call_count"],
            "input_tokens": final["input_tokens"],
            "output_tokens": final["output_tokens"],
            "cache_read_input_tokens": final.get("cache_read_input_tokens", 0),
            "cache_creation_input_tokens": final.get("cache_creation_input_tokens", 0),
            "finished_at": utcnow(),
            "error_code": final.get("error_code"),
        }
        with self._factory.begin() as session:
            run = session.get_one(InvestigationRunRow, run_id, with_for_update=True)
            incident = session.get_one(IncidentRow, incident_id)
            report: DiagnosisReport | None = None
            if status == "COMPLETED" and final.get("report"):
                report = DiagnosisReport.model_validate(final["report"])
                run_state = RunState.COMPLETED
            elif status in {"BUDGET_EXCEEDED", "RUNNING"}:
                run_state = RunState.BUDGET_EXCEEDED
            else:
                run_state = RunState.FAILED
            transition(
                session,
                InvestigationRunRow,
                run_id,
                expected_state=RunState.RUNNING,
                expected_version=run.version,
                target_state=run_state,
                audit=_audit(
                    "reinvestigation_finished",
                    usage["error_code"] or (report.diagnosis.root_cause_id if report else None),
                    incident_id=incident_id,
                    run_id=run_id,
                ),
                extra_values=usage,
            )
            if report is not None:
                # The incident stays where automation left it; the new diagnosis
                # is information for the person handling it.
                session.add(
                    DiagnosisRow(
                        id=new_id("diag"),
                        run_id=run_id,
                        root_cause_id=report.diagnosis.root_cause_id,
                        report=report.model_dump(mode="json"),
                        cited_evidence_ids=sorted(report.cited_evidence_ids()),
                    )
                )
            return RunOutcome(run_id, run_state, IncidentState(incident.state), usage["error_code"])

    def _finish(self, incident_id: str, run_id: str, final: dict[str, Any]) -> RunOutcome:
        status = final["status"]
        usage = {
            "step_count": final["step_count"],
            "tool_call_count": final["tool_call_count"],
            "input_tokens": final["input_tokens"],
            "output_tokens": final["output_tokens"],
            "cache_read_input_tokens": final.get("cache_read_input_tokens", 0),
            "cache_creation_input_tokens": final.get("cache_creation_input_tokens", 0),
            "finished_at": utcnow(),
            "error_code": final.get("error_code"),
        }
        with self._factory.begin() as session:
            run = session.get_one(InvestigationRunRow, run_id, with_for_update=True)
            incident = session.get_one(IncidentRow, incident_id, with_for_update=True)
            if status == "COMPLETED" and final.get("report"):
                report = DiagnosisReport.model_validate(final["report"])
                run_state = RunState.COMPLETED
            elif status in {"BUDGET_EXCEEDED", "RUNNING"}:
                # Report partial findings instead of failing silently (§6.1 graph).
                report = DiagnosisReport.model_validate(
                    {
                        "incident_id": incident_id,
                        "diagnosis": {
                            "root_cause_id": INSUFFICIENT,
                            "affected_component": incident.service_id,
                            "confidence_level": "low",
                            "summary": "Investigation budget exhausted before a conclusion.",
                            "supporting_evidence_ids": final["evidence_ids"],
                            "unresolved_questions": ["Budget exhausted; review evidence."],
                        },
                        "needs_human_review": True,
                    }
                )
                run_state = RunState.BUDGET_EXCEEDED
            else:
                report = None
                run_state = RunState.FAILED

            transition(
                session,
                InvestigationRunRow,
                run_id,
                expected_state=RunState.RUNNING,
                expected_version=run.version,
                target_state=run_state,
                audit=_audit(
                    "run_finished", usage["error_code"], incident_id=incident_id, run_id=run_id
                ),
                extra_values=usage,
            )

            if report is None:
                incident_state = IncidentState.FAILED
                transition(
                    session,
                    IncidentRow,
                    incident_id,
                    expected_state=IncidentState.INVESTIGATING,
                    expected_version=incident.version,
                    target_state=incident_state,
                    audit=_audit("investigation_failed", usage["error_code"], run_id=run_id),
                    extra_values={"escalation_reason": usage["error_code"] or "run_failed"},
                )
                return RunOutcome(run_id, run_state, incident_state, usage["error_code"])

            diagnosis_id = new_id("diag")
            session.add(
                DiagnosisRow(
                    id=diagnosis_id,
                    run_id=run_id,
                    root_cause_id=report.diagnosis.root_cause_id,
                    report=report.model_dump(mode="json"),
                    cited_evidence_ids=sorted(report.cited_evidence_ids()),
                )
            )
            session.flush()
            version = transition(
                session,
                IncidentRow,
                incident_id,
                expected_state=IncidentState.INVESTIGATING,
                expected_version=incident.version,
                target_state=IncidentState.DIAGNOSED,
                audit=_audit(
                    "incident_diagnosed",
                    report.diagnosis.root_cause_id,
                    run_id=run_id,
                    diagnosis_id=diagnosis_id,
                ),
            )
            incident_state = self._route(session, incident_id, version, report, diagnosis_id)
            return RunOutcome(run_id, run_state, incident_state, usage["error_code"])

    def _route(
        self,
        session: Session,
        incident_id: str,
        version: int,
        report: DiagnosisReport,
        diagnosis_id: str,
    ) -> IncidentState:
        """Decide the post-diagnosis incident state; remediation waits for P3 policy."""
        if report.proposed_action is not None:
            # The incident stays DIAGNOSED until deterministic policy evaluates
            # the proposal; the agent never authorizes its own action.
            session.add(
                OutboxJobRow(
                    id=new_id("job"),
                    kind=EVALUATE_PROPOSAL_JOB,
                    aggregate_id=incident_id,
                    dedup_key=f"{EVALUATE_PROPOSAL_JOB}:{diagnosis_id}",
                    payload={"incident_id": incident_id, "diagnosis_id": diagnosis_id},
                )
            )
            return IncidentState.DIAGNOSED
        if report.diagnosis.root_cause_id == INSUFFICIENT:
            version = transition(
                session,
                IncidentRow,
                incident_id,
                expected_state=IncidentState.DIAGNOSED,
                expected_version=version,
                target_state=IncidentState.INSUFFICIENT_EVIDENCE,
                audit=_audit("insufficient_evidence", diagnosis_id=diagnosis_id),
            )
            transition(
                session,
                IncidentRow,
                incident_id,
                expected_state=IncidentState.INSUFFICIENT_EVIDENCE,
                expected_version=version,
                target_state=IncidentState.ESCALATED,
                audit=_audit("incident_escalated", INSUFFICIENT, diagnosis_id=diagnosis_id),
                extra_values={"escalation_reason": INSUFFICIENT},
            )
            return IncidentState.ESCALATED
        transition(
            session,
            IncidentRow,
            incident_id,
            expected_state=IncidentState.DIAGNOSED,
            expected_version=version,
            target_state=IncidentState.DIAGNOSED_ONLY,
            audit=_audit("diagnosed_without_action", diagnosis_id=diagnosis_id),
        )
        return IncidentState.DIAGNOSED_ONLY


def with_prior_context(message: dict[str, Any], prior: dict[str, Any]) -> dict[str, Any]:
    """Add what earlier runs and the chat found, framed as data to re-check."""
    payload, _ = truncate_utf8(
        redact(json.dumps(prior, sort_keys=True, default=str)), PRIOR_CONTEXT_BYTES
    )
    text = (
        "This is a re-investigation a person requested after automation handed the "
        "incident over. The earlier diagnosis and evidence below may be wrong or stale: "
        "use them as leads and confirm what you rely on with your own tool calls, which "
        "are the only evidence you may cite. The unverified_hypothesis was written by a "
        "person or the chat agent and must be tested, not assumed. A proposed action "
        "will be shown to the person but not executed automatically.\n"
        f"<untrusted_prior_findings>\n{payload}\n</untrusted_prior_findings>"
    )
    return {**message, "content": [*message["content"], {"type": "text", "text": text}]}
