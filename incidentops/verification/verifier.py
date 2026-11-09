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


class VerifySpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lag_max: Annotated[int, Field(ge=0)]
    observation_window_seconds: Annotated[int, Field(ge=10, le=3600)]
    sample_min_count: Annotated[int, Field(ge=2)]
    recovery_timeout_seconds: Annotated[int, Field(ge=10, le=3600)]
    poll_interval_seconds: Annotated[int, Field(ge=1, le=300)]


class VerifyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    specs: dict[str, VerifySpec]


def load_verify_config(path: Path = DEFAULT_SPEC_PATH) -> VerifyConfig:
    return VerifyConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


@dataclass(frozen=True)
class CheckSet:
    checks: list[VerificationCheck]

    @property
    def passed(self) -> bool:
        return all(c.passed is True for c in self.checks)

    @property
    def unknown(self) -> bool:
        return any(c.passed is None for c in self.checks)


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
    ) -> None:
        self._factory = factory
        self._catalog = catalog
        self._backend = backend
        self._inspector = inspector
        self._config = config
        self._clock = clock
        self._sleep = sleep

    async def _checks(
        self, target_id: str, expected_generation: str | None, spec: VerifySpec
    ) -> CheckSet:
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

    async def verify(self, action_id: str) -> VerificationOutcome | None:
        with self._factory() as session:
            action = session.get_one(ActionProposalRow, action_id)
            incident = session.get_one(IncidentRow, action.incident_id)
            operation = session.scalars(
                select(ExternalOperationRow).where(ExternalOperationRow.action_id == action_id)
            ).first()
        if action.state != ActionState.SUCCEEDED or incident.state != IncidentState.VERIFYING:
            return None
        spec = self._config.specs.get(action.action_type)
        expected_generation = receipt_generation(operation.receipt if operation else None)

        started = self._clock()
        deadline = started + timedelta(seconds=spec.recovery_timeout_seconds) if spec else started
        checks = CheckSet(
            [VerificationCheck(check_id="spec", passed=None, observed="no verification spec")]
        )
        while spec is not None:
            checks = await self._checks(action.target_id, expected_generation, spec)
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
