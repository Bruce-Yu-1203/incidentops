"""Read-only "re-check now" for handed-over incidents (ADR-021).

Re-evaluates the incident's metric postconditions once against Prometheus.
It uses no model and no executor, never changes the incident, and records an
audit event so the check shows in the incident timeline. A person still
decides whether to close the incident.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final, Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from incidentops.auth.service import AuthenticatedUser
from incidentops.domain.catalog import ServiceCatalog, ServiceKind, UnknownServiceError
from incidentops.domain.ids import new_id
from incidentops.domain.schemas import VerificationCheck, VerificationOutcome
from incidentops.persistence.models import (
    ActionProposalRow,
    AlertEventRow,
    AuditEventRow,
    IncidentRow,
)
from incidentops.tools.backends import TelemetryBackend
from incidentops.tools.templates import TEMPLATES
from incidentops.verification.verifier import (
    CheckSet,
    MetricCheck,
    VerifyConfig,
    VerifySpec,
    evaluate_lag,
    evaluate_metric,
)

RECHECK_EVENT: Final = "incident_rechecked"
STEP_SECONDS: Final = 5

# Used when the incident never got an action with a verification spec.
_FALLBACK_SPEC: Final = VerifySpec(
    observation_window_seconds=60,
    sample_min_count=5,
    recovery_timeout_seconds=60,
    poll_interval_seconds=10,
)
_Bound = tuple[Literal["metric_max", "metric_min"], str, float]
_FALLBACK_CHECKS: Final[dict[ServiceKind, tuple[_Bound, ...]]] = {
    ServiceKind.HTTP_API: (
        ("metric_max", "api_error_ratio", 0.02),
        ("metric_max", "api_latency_p95", 0.5),
    ),
    ServiceKind.POSTGRES: (("metric_max", "postgres_exclusive_locks", 0),),
    ServiceKind.REDIS: (("metric_min", "redis_up", 1),),
}


class RecheckPlan(BaseModel):
    incident_id: str
    service_id: str
    action_type: str | None
    lag: bool
    metric_checks: list[MetricCheck]
    spec: VerifySpec


class RecheckResult(BaseModel):
    incident_id: str
    outcome: VerificationOutcome
    checks: list[VerificationCheck]
    checked_at: datetime
    based_on: str
    alert_cleared_at: datetime | None


def alert_cleared_at(session: Session, incident_id: str) -> datetime | None:
    """When the latest alert delivery is ``resolved``, the time it arrived."""
    latest = session.scalars(
        select(AlertEventRow)
        .where(AlertEventRow.incident_id == incident_id)
        .order_by(AlertEventRow.received_at.desc())
        .limit(1)
    ).first()
    if latest is None or latest.status != "resolved":
        return None
    return latest.received_at


def plan_recheck(
    session: Session, incident_id: str, catalog: ServiceCatalog, config: VerifyConfig
) -> RecheckPlan | None:
    incident = session.get(IncidentRow, incident_id)
    if incident is None:
        return None
    action = session.scalars(
        select(ActionProposalRow)
        .where(ActionProposalRow.incident_id == incident_id)
        .order_by(ActionProposalRow.created_at.desc())
        .limit(1)
    ).first()
    spec = config.specs.get(action.action_type) if action is not None else None
    if spec is not None:
        metric_checks = [c for c in spec.checks if isinstance(c, MetricCheck)]
        return RecheckPlan(
            incident_id=incident_id,
            service_id=action.target_id if action else incident.service_id,
            action_type=action.action_type if action else None,
            lag=not spec.checks,
            metric_checks=metric_checks,
            spec=spec,
        )
    try:
        kind: ServiceKind | None = catalog.get(incident.service_id).kind
    except UnknownServiceError:
        kind = None
    checks = [
        MetricCheck(
            kind=check_kind,
            check_id=f"{template}_ok",
            template_id=template,
            service_id=incident.service_id,
            bound=bound,
        )
        for check_kind, template, bound in (_FALLBACK_CHECKS.get(kind, ()) if kind else ())
    ]
    return RecheckPlan(
        incident_id=incident_id,
        service_id=incident.service_id,
        action_type=None,
        lag=kind is ServiceKind.KAFKA_CONSUMER,
        metric_checks=checks,
        spec=_FALLBACK_SPEC,
    )


async def run_checks(
    plan: RecheckPlan,
    catalog: ServiceCatalog,
    backend: TelemetryBackend,
    now: datetime,
) -> CheckSet:
    start = now - timedelta(seconds=plan.spec.observation_window_seconds)
    results: list[VerificationCheck] = []
    if plan.lag:
        try:
            series = await backend.prometheus_range(
                TEMPLATES["kafka_group_lag"].render(catalog.get(plan.service_id)),
                start,
                now,
                STEP_SECONDS,
            )
            points = [value for _, value in series[0].points] if series else []
            results.append(evaluate_lag(points, plan.spec))
        except Exception:
            results.append(
                VerificationCheck(
                    check_id="lag_recovered", passed=None, observed="prometheus unavailable"
                )
            )
    for check in plan.metric_checks:
        try:
            series = await backend.prometheus_range(
                TEMPLATES[check.template_id].render(catalog.get(check.service_id)),
                start,
                now,
                STEP_SECONDS,
            )
            points = [value for _, value in series[0].points] if series else []
            results.append(evaluate_metric(check, points, plan.spec))
        except Exception:
            results.append(
                VerificationCheck(
                    check_id=check.check_id, passed=None, observed="prometheus unavailable"
                )
            )
    if not results:
        results.append(
            VerificationCheck(
                check_id="no_checks", passed=None, observed="no metric checks for this service"
            )
        )
    return CheckSet(results)


def outcome_of(checks: CheckSet) -> VerificationOutcome:
    if checks.passed:
        return VerificationOutcome.PASSED
    if checks.unknown:
        return VerificationOutcome.UNKNOWN
    return VerificationOutcome.VERIFICATION_FAILED


def record_recheck(
    session: Session, plan: RecheckPlan, user: AuthenticatedUser, outcome: VerificationOutcome
) -> None:
    session.add(
        AuditEventRow(
            id=new_id("aud"),
            actor=user.principal,
            event_type=RECHECK_EVENT,
            resource_type=IncidentRow.__tablename__,
            resource_id=plan.incident_id,
            decision=outcome.value,
            reason=None,
            correlation_ids={
                "incident_id": plan.incident_id,
                "based_on": plan.action_type or "service_defaults",
            },
        )
    )
