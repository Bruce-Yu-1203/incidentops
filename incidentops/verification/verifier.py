"""Recovery verifier (architecture §10).

Independent of the agent and of the executor's success message: it reads fresh
target status and Prometheus series with its own read-only identity and only
a PASSED verification may resolve an incident. Missing or stale data yields
UNKNOWN, never success (R-05).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from incidentops.actions.targets import TargetInspector
from incidentops.domain.catalog import ServiceCatalog
from incidentops.domain.ids import new_id
from incidentops.domain.schemas import VerificationCheck, VerificationOutcome
from incidentops.domain.state_machines import ActionState, IncidentState
from incidentops.execution.executor import receipt_generation
from incidentops.grouping import (
    SPLIT_BY_VERIFIER_NOTE,
    group_of_primary,
    split_member,
    still_firing,
)
from incidentops.persistence.models import (
    ActionProposalRow,
    ExternalOperationRow,
    IncidentRow,
    VerificationRow,
)
from incidentops.persistence.transitions import AuditSpec, transition
from incidentops.time import utcnow
from incidentops.tools.backends import TelemetryBackend
from incidentops.tools.templates import TEMPLATES

ACTOR: Final = "service:verifier"
DEFAULT_SPEC_PATH = Path(__file__).resolve().parents[2] / "config" / "verification.yaml"


class MetricCheck(BaseModel):
    """A bound on a predeclared metric template over the observation window."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["metric_max", "metric_min", "metric_not_rising"]
    check_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    template_id: str
    service_id: str
    # max/min bound, or allowed growth (last - first) for metric_not_rising.
    bound: float


class RevisionCheck(BaseModel):
    """The target must report the revision recorded in the execution receipt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["target_revision"]
    check_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    target_id: str
    equals: str | None = None  # None: the receipt's revision_after


CheckSpec = Annotated[MetricCheck | RevisionCheck, Field(discriminator="kind")]


class VerifySpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Generic checks; when empty, the consumer lag checks below apply (B-F03).
    checks: tuple[CheckSpec, ...] = ()
    lag_max: Annotated[int, Field(ge=0)] = 100
    observation_window_seconds: Annotated[int, Field(ge=10, le=3600)]
    sample_min_count: Annotated[int, Field(ge=2)]
    recovery_timeout_seconds: Annotated[int, Field(ge=10, le=3600)]
    poll_interval_seconds: Annotated[int, Field(ge=1, le=300)]


class VerifyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    # "<action_type>" or "<action_type>:<target_id>" (target-specific wins).
    specs: dict[str, VerifySpec]

    def spec_for(self, action_type: str, target_id: str) -> VerifySpec | None:
        return self.specs.get(f"{action_type}:{target_id}") or self.specs.get(action_type)


def load_verify_config(path: Path = DEFAULT_SPEC_PATH) -> VerifyConfig:
    return VerifyConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


@dataclass(frozen=True)
class CheckSet:
    checks: list[VerificationCheck]

    @property
    def judged(self) -> list[VerificationCheck]:
        return [c for c in self.checks if not c.skipped]

    @property
    def passed(self) -> bool:
        judged = self.judged
        return bool(judged) and all(c.passed is True for c in judged)

    @property
    def unknown(self) -> bool:
        return any(c.passed is None for c in self.judged)

    @property
    def skipped(self) -> list[str]:
        return [c.check_id for c in self.checks if c.skipped]


def evaluate_metric(check: MetricCheck, points: list[float], spec: VerifySpec) -> VerificationCheck:
    """Sustained bound over the last ``sample_min_count`` samples (or growth limit)."""
    if len(points) < spec.sample_min_count:
        return VerificationCheck(
            check_id=check.check_id,
            passed=None,
            observed=f"only {len(points)} fresh samples of {check.template_id}",
            sample_count=len(points),
        )
    tail = points[-spec.sample_min_count :]
    if check.kind == "metric_max":
        passed = max(tail) <= check.bound
        observed = f"{check.template_id} tail max {max(tail):.4g} (bound <= {check.bound:g})"
    elif check.kind == "metric_min":
        passed = min(tail) >= check.bound
        observed = f"{check.template_id} tail min {min(tail):.4g} (bound >= {check.bound:g})"
    else:
        growth = tail[-1] - tail[0]
        passed = growth <= check.bound
        observed = f"{check.template_id} grew {growth:.4g} over the tail (limit {check.bound:g})"
    return VerificationCheck(
        check_id=check.check_id, passed=passed, observed=observed, sample_count=len(points)
    )


def evaluate_lag(points: list[float], spec: VerifySpec) -> VerificationCheck:
    """Lag must be within bound for the last ``sample_min_count`` samples and not rising."""
    if len(points) < spec.sample_min_count:
        return VerificationCheck(
            check_id="lag_recovered",
            passed=None,
            observed=f"only {len(points)} fresh samples",
            sample_count=len(points),
        )
    tail = points[-spec.sample_min_count :]
    sustained = all(value <= spec.lag_max for value in tail)
    not_rising = points[-1] <= points[0]
    return VerificationCheck(
        check_id="lag_recovered",
        passed=sustained and not_rising,
        observed=f"lag {points[0]:.0f} -> {points[-1]:.0f}, tail max {max(tail):.0f} "
        f"(bound {spec.lag_max})",
        sample_count=len(points),
    )


class RecoveryVerifier:
    def __init__(
        self,
        factory: sessionmaker[Session],
        catalog: ServiceCatalog,
        backend: TelemetryBackend,
        inspector: TargetInspector,
        config: VerifyConfig,
        clock: Callable[[], datetime] = utcnow,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        member_recovery_seconds: int = 180,
        member_poll_seconds: int = 15,
    ) -> None:
        self._member_recovery_seconds = member_recovery_seconds
        self._member_poll_seconds = member_poll_seconds
        self._factory = factory
        self._catalog = catalog
        self._backend = backend
        self._inspector = inspector
        self._config = config
        self._clock = clock
        self._sleep = sleep

    async def _generic_checks(
        self,
        expected_revision: str | None,
        spec: VerifySpec,
        target_id: str = "",
        delegated: frozenset[str] = frozenset(),
    ) -> CheckSet:
        results: list[VerificationCheck] = []
        now = self._clock()
        for check in spec.checks:
            if (
                isinstance(check, MetricCheck)
                and check.service_id != target_id
                and check.service_id in delegated
            ):
                # B' (ADR-030): another service that is an unsplit member of this
                # incident's group is judged by its own alerts after this fix
                # (G4), so a second fault there cannot fail this fix.
                results.append(
                    VerificationCheck(
                        check_id=check.check_id,
                        passed=None,
                        observed=f"skipped: {check.service_id} is a group member; "
                        "its own alerts are checked after this fix",
                        skipped=True,
                    )
                )
                continue
            if isinstance(check, RevisionCheck):
                state = await self._inspector.inspect(check.target_id)
                wanted = check.equals or expected_revision
                if state is None or wanted is None:
                    results.append(
                        VerificationCheck(
                            check_id=check.check_id,
                            passed=None,
                            observed="revision unavailable",
                        )
                    )
                    continue
                results.append(
                    VerificationCheck(
                        check_id=check.check_id,
                        passed=state["revision"] == wanted,
                        observed=f"revision={state['revision']} expected={wanted}",
                    )
                )
                continue
            try:
                series = await self._backend.prometheus_range(
                    TEMPLATES[check.template_id].render(self._catalog.get(check.service_id)),
                    now - timedelta(seconds=spec.observation_window_seconds),
                    now,
                    5,
                )
                points = [value for _, value in series[0].points] if series else []
                results.append(evaluate_metric(check, points, spec))
            except Exception:
                results.append(
                    VerificationCheck(
                        check_id=check.check_id, passed=None, observed="prometheus unavailable"
                    )
                )
        return CheckSet(results)

    async def _checks(
        self,
        target_id: str,
        expected_generation: str | None,
        spec: VerifySpec,
        delegated: frozenset[str] = frozenset(),
    ) -> CheckSet:
        if spec.checks:
            return await self._generic_checks(expected_generation, spec, target_id, delegated)
        service = self._catalog.get(target_id)
        checks: list[VerificationCheck] = []

        status = await self._inspector.inspect(target_id)
        if status is None:
            checks.append(
                VerificationCheck(
                    check_id="worker_running", passed=None, observed="status unavailable"
                )
            )
        else:
            generation_ok = expected_generation is None or status["revision"] == expected_generation
            checks.append(
                VerificationCheck(
                    check_id="worker_running",
                    passed=status.get("paused") is False and generation_ok,
                    observed=f"paused={status.get('paused')} generation={status['revision']} "
                    f"expected={expected_generation}",
                )
            )

        try:
            brokers = await self._backend.prometheus_instant(
                TEMPLATES["kafka_broker_count"].render(self._catalog.get("lab-kafka"))
            )
            count = brokers[0].points[-1][1] if brokers and brokers[0].points else None
            checks.append(
                VerificationCheck(
                    check_id="broker_healthy",
                    passed=None if count is None else count >= 1,
                    observed=f"brokers={count}",
                )
            )
        except Exception:
            checks.append(
                VerificationCheck(
                    check_id="broker_healthy", passed=None, observed="prometheus unavailable"
                )
            )

        try:
            now = self._clock()
            series = await self._backend.prometheus_range(
                TEMPLATES["kafka_group_lag"].render(service),
                now - timedelta(seconds=spec.observation_window_seconds),
                now,
                5,
            )
            points = [value for _, value in series[0].points] if series else []
            checks.append(evaluate_lag(points, spec))
        except Exception:
            checks.append(
                VerificationCheck(
                    check_id="lag_recovered", passed=None, observed="prometheus unavailable"
                )
            )
        return CheckSet(checks)

    def _member_services(self, incident_id: str) -> frozenset[str]:
        """Services of the unsplit GROUPED members of the group this incident leads."""
        with self._factory() as session:
            group = group_of_primary(session, incident_id)
            if group is None:
                return frozenset()
            return frozenset(
                session.scalars(
                    select(IncidentRow.service_id).where(
                        IncidentRow.group_id == group.id,
                        IncidentRow.id != incident_id,
                        IncidentRow.group_split_at.is_(None),
                        IncidentRow.state == IncidentState.GROUPED.value,
                    )
                )
            )

    async def verify(self, action_id: str) -> VerificationOutcome | None:
        with self._factory() as session:
            action = session.get_one(ActionProposalRow, action_id)
            incident = session.get_one(IncidentRow, action.incident_id)
            operation = session.scalars(
                select(ExternalOperationRow).where(ExternalOperationRow.action_id == action_id)
            ).first()
        if action.state != ActionState.SUCCEEDED or incident.state != IncidentState.VERIFYING:
            return None
        spec = self._config.spec_for(action.action_type, action.target_id)
        expected_generation = receipt_generation(operation.receipt if operation else None)

        started = self._clock()
        deadline = started + timedelta(seconds=spec.recovery_timeout_seconds) if spec else started
        checks = CheckSet(
            [VerificationCheck(check_id="spec", passed=None, observed="no verification spec")]
        )
        while spec is not None:
            checks = await self._checks(
                action.target_id, expected_generation, spec, self._member_services(incident.id)
            )
            if checks.passed or self._clock() >= deadline:
                break
            await self._sleep(spec.poll_interval_seconds)

        if checks.passed:
            outcome = VerificationOutcome.PASSED
        elif checks.unknown:
            outcome = VerificationOutcome.UNKNOWN
        else:
            outcome = VerificationOutcome.VERIFICATION_FAILED
        self._record(action_id, outcome, checks, started)
        return outcome

    def _record(
        self, action_id: str, outcome: VerificationOutcome, checks: CheckSet, started: datetime
    ) -> None:
        with self._factory.begin() as session:
            action = session.get_one(ActionProposalRow, action_id)
            incident = session.get_one(IncidentRow, action.incident_id, with_for_update=True)
            verification_id = new_id("ver")
            session.add(
                VerificationRow(
                    id=verification_id,
                    action_id=action_id,
                    outcome=outcome.value,
                    checks=[c.model_dump(mode="json") for c in checks.checks],
                    window_start=started,
                    window_end=self._clock(),
                    evidence_ids=[],
                )
            )
            session.flush()
            if incident.state != IncidentState.VERIFYING:
                return
            audit = AuditSpec(
                actor=ACTOR,
                event_type="verification_completed",
                reason=f"delegated to group member checks: {', '.join(checks.skipped)}"
                if checks.skipped
                else None,
                decision=outcome.value,
                action_hash=action.action_hash,
                correlation_ids={"action_id": action_id, "verification_id": verification_id},
            )
            if outcome is VerificationOutcome.PASSED:
                transition(
                    session,
                    IncidentRow,
                    incident.id,
                    expected_state=IncidentState.VERIFYING,
                    expected_version=incident.version,
                    target_state=IncidentState.RESOLVED,
                    audit=audit,
                )
            else:
                transition(
                    session,
                    IncidentRow,
                    incident.id,
                    expected_state=IncidentState.VERIFYING,
                    expected_version=incident.version,
                    target_state=IncidentState.ESCALATED,
                    audit=audit,
                    extra_values={"escalation_reason": outcome.value.lower()},
                )

    # -- group members (ADR-030, guard G4) -------------------------------------------

    async def verify_member(self, incident_id: str, primary_id: str) -> IncidentState | None:
        """Resolve a GROUPED member whose alerts recovered, or split it out.

        Runs after the primary's verification PASSED. Alertmanager reports a
        cleared rule one group interval later, so the member gets a bounded
        wait for its alerts to resolve before it is split out and investigated
        on its own ("not explained by the group primary's fix").
        """
        deadline = self._clock() + timedelta(seconds=self._member_recovery_seconds)
        while True:
            with self._factory() as session:
                member = session.get(IncidentRow, incident_id)
                if (
                    member is None
                    or member.state != IncidentState.GROUPED
                    or member.group_split_at is not None
                ):
                    return None
                firing = still_firing(session, incident_id)
            if not firing or self._clock() >= deadline:
                break
            await self._sleep(self._member_poll_seconds)

        with self._factory.begin() as session:
            member = session.get(IncidentRow, incident_id, with_for_update=True)
            if member is None or member.state != IncidentState.GROUPED:
                return None
            if member.group_split_at is not None:
                return None
            if still_firing(session, incident_id):
                split_member(
                    session,
                    member,
                    actor=ACTOR,
                    reason=f"alerts still firing after group primary {primary_id} recovered",
                    note=SPLIT_BY_VERIFIER_NOTE,
                )
                return IncidentState.RECEIVED
            transition(
                session,
                IncidentRow,
                incident_id,
                expected_state=IncidentState.GROUPED,
                expected_version=member.version,
                target_state=IncidentState.RESOLVED,
                audit=AuditSpec(
                    actor=ACTOR,
                    event_type="group_member_resolved",
                    reason=f"alerts resolved after group primary {primary_id} recovered",
                    correlation_ids={
                        "primary_incident_id": primary_id,
                        "group_id": member.group_id or "",
                    },
                ),
            )
            return IncidentState.RESOLVED
