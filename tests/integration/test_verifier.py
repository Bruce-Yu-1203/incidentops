"""Independent recovery verification (FR-024, R-05)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from incidentops.domain.catalog import load_catalog
from incidentops.domain.schemas import VerificationOutcome
from incidentops.persistence.models import ActionProposalRow, IncidentRow, VerificationRow
from incidentops.tools.backends import Series
from incidentops.verification.verifier import RecoveryVerifier, VerifyConfig, VerifySpec
from tests.integration.helpers import FakeAdapter, FakeInspector
from tests.integration.test_executor import _executor, _queued_action
from tests.unit.tool_fakes import FakeBackend

pytestmark = pytest.mark.integration

SPEC = VerifySpec(
    lag_max=100,
    observation_window_seconds=60,
    sample_min_count=5,
    recovery_timeout_seconds=30,
    poll_interval_seconds=10,
)


class LagBackend(FakeBackend):
    def __init__(self, lag: list[float], brokers: float = 1.0, broken: bool = False) -> None:
        super().__init__()
        self.lag_points = lag
        self.brokers = brokers
        self.broken = broken

    async def prometheus_range(self, query: str, start: Any, end: Any, step: int) -> list[Series]:
        if self.broken:
            raise ConnectionError("prometheus down")
        return [Series({}, [(float(i), v) for i, v in enumerate(self.lag_points)])]

    async def prometheus_instant(self, query: str) -> list[Series]:
        if self.broken:
            raise ConnectionError("prometheus down")
        return [Series({}, [(0.0, self.brokers)])]


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 9, 12, 0, tzinfo=datetime.now().astimezone().tzinfo)

    def __call__(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


async def _verifying_action(factory: sessionmaker[Session]) -> str:
    action_id = await _queued_action(factory)
    await _executor(factory, FakeAdapter()).execute(action_id)
    return action_id


def _verifier(
    factory: sessionmaker[Session], backend: LagBackend, inspector: FakeInspector
) -> tuple[RecoveryVerifier, FakeClock]:
    clock = FakeClock()
    verifier = RecoveryVerifier(
        factory,
        load_catalog(),
        backend,
        inspector,
        VerifyConfig(schema_version=1, specs={"resume_test_consumer": SPEC}),
        clock=clock,
        sleep=clock.sleep,
    )
    return verifier, clock


def _incident(factory: sessionmaker[Session], action_id: str) -> IncidentRow:
    with factory() as session:
        action = session.get_one(ActionProposalRow, action_id)
        return session.get_one(IncidentRow, action.incident_id)


async def test_sustained_recovery_resolves_incident(session_factory: sessionmaker[Session]) -> None:
    action_id = await _verifying_action(session_factory)
    verifier, _ = _verifier(
        session_factory,
        LagBackend([1800, 600, 40, 5, 0, 0, 0]),
        FakeInspector(revision="gen-3", paused=False),
    )
    assert await verifier.verify(action_id) is VerificationOutcome.PASSED
    assert _incident(session_factory, action_id).state == "RESOLVED"
    with session_factory() as session:
        row = session.scalars(select(VerificationRow)).one()
    assert {c["check_id"] for c in row.checks} == {
        "worker_running",
        "broker_healthy",
        "lag_recovered",
    }


@pytest.mark.parametrize(
    ("backend", "inspector", "outcome", "reason"),
    [
        # Lag never drains (e.g. producer outpaces the consumer).
        (
            LagBackend([1800, 1900, 2000, 2100, 2200]),
            FakeInspector("gen-3", False),
            VerificationOutcome.VERIFICATION_FAILED,
            "verification_failed",
        ),
        # The worker reports paused even though the action "succeeded".
        (
            LagBackend([0, 0, 0, 0, 0]),
            FakeInspector("gen-3", True),
            VerificationOutcome.VERIFICATION_FAILED,
            "verification_failed",
        ),
        # Someone else changed the worker after our action.
        (
            LagBackend([0, 0, 0, 0, 0]),
            FakeInspector("gen-9", False),
            VerificationOutcome.VERIFICATION_FAILED,
            "verification_failed",
        ),
        # R-05: telemetry unavailable is UNKNOWN, never RESOLVED.
        (
            LagBackend([], broken=True),
            FakeInspector("gen-3", False),
            VerificationOutcome.UNKNOWN,
            "unknown",
        ),
    ],
)
async def test_unrecovered_or_unknown_escalates(
    session_factory: sessionmaker[Session],
    backend: LagBackend,
    inspector: FakeInspector,
    outcome: VerificationOutcome,
    reason: str,
) -> None:
    action_id = await _verifying_action(session_factory)
    verifier, clock = _verifier(session_factory, backend, inspector)
    started = clock.now
    assert await verifier.verify(action_id) is outcome
    incident = _incident(session_factory, action_id)
    assert (incident.state, incident.escalation_reason) == ("ESCALATED", reason)
    assert clock.now - started >= timedelta(seconds=SPEC.recovery_timeout_seconds)


async def test_only_verifying_incidents_are_verified(
    session_factory: sessionmaker[Session],
) -> None:
    action_id = await _queued_action(session_factory)  # not executed yet
    verifier, _ = _verifier(session_factory, LagBackend([0] * 5), FakeInspector("gen-3", False))
    assert await verifier.verify(action_id) is None


@pytest.mark.parametrize(
    ("points", "revision", "outcome"),
    [
        ([0.3, 0.0, 0.0, 0.0, 0.0, 0.0], "gen-3", VerificationOutcome.PASSED),
        ([0.3, 0.3, 0.3, 0.3, 0.3], "gen-3", VerificationOutcome.VERIFICATION_FAILED),
        ([0.0, 0.0, 0.0, 0.0, 0.0], "gen-9", VerificationOutcome.VERIFICATION_FAILED),
        ([0.0, 0.0], "gen-3", VerificationOutcome.UNKNOWN),
    ],
)
async def test_generic_checks_from_config(
    session_factory: sessionmaker[Session],
    points: list[float],
    revision: str,
    outcome: VerificationOutcome,
) -> None:
    from incidentops.verification.verifier import MetricCheck, RevisionCheck

    action_id = await _verifying_action(session_factory)
    spec = VerifySpec(
        observation_window_seconds=60,
        sample_min_count=5,
        recovery_timeout_seconds=30,
        poll_interval_seconds=10,
        checks=(
            RevisionCheck(
                kind="target_revision", check_id="revision", target_id="persist-demo-consumer"
            ),
            MetricCheck(
                kind="metric_max",
                check_id="errors",
                template_id="api_error_ratio",
                service_id="lab-api",
                bound=0.02,
            ),
        ),
    )
    clock = FakeClock()
    verifier = RecoveryVerifier(
        session_factory,
        load_catalog(),
        LagBackend(points),
        FakeInspector(revision, paused=False),
        VerifyConfig(schema_version=1, specs={"resume_test_consumer": spec}),
        clock=clock,
        sleep=clock.sleep,
    )
    assert await verifier.verify(action_id) is outcome


def test_repository_verification_specs_reference_known_templates() -> None:
    from incidentops.tools.templates import TEMPLATES
    from incidentops.verification.verifier import MetricCheck, load_verify_config

    catalog = load_catalog()
    for spec in load_verify_config().specs.values():
        for check in spec.checks:
            if isinstance(check, MetricCheck):
                assert check.template_id in TEMPLATES
                TEMPLATES[check.template_id].render(catalog.get(check.service_id))


# -- B' (ADR-030): checks on a grouped member are left to the member's own check -----


def _errors_spec() -> VerifySpec:
    from incidentops.verification.verifier import MetricCheck, RevisionCheck

    return VerifySpec(
        observation_window_seconds=60,
        sample_min_count=5,
        recovery_timeout_seconds=30,
        poll_interval_seconds=10,
        checks=(
            RevisionCheck(
                kind="target_revision", check_id="revision", target_id="persist-demo-consumer"
            ),
            MetricCheck(
                kind="metric_max",
                check_id="api_errors_recovered",
                template_id="api_error_ratio",
                service_id="lab-api",
                bound=0.02,
            ),
        ),
    )


def _add_member(factory: sessionmaker[Session], primary: IncidentRow, state: str) -> str:
    from incidentops.domain.ids import new_id

    member_id = new_id("inc")
    with factory.begin() as session:
        session.add(
            IncidentRow(
                id=member_id,
                dedup_key="fault-lab:lab-api",
                service_id="lab-api",
                environment="fault-lab",
                severity="critical",
                state=state,
                version=1,
                group_id=primary.group_id,
                symptom_class="dependency",
            )
        )
    return member_id


def _errors_verifier(factory: sessionmaker[Session]) -> RecoveryVerifier:
    clock = FakeClock()
    return RecoveryVerifier(
        factory,
        load_catalog(),
        LagBackend([0.5] * 10),  # lab-api keeps failing
        FakeInspector("gen-3", paused=False),
        VerifyConfig(schema_version=1, specs={"resume_test_consumer": _errors_spec()}),
        clock=clock,
        sleep=clock.sleep,
    )


async def test_check_on_a_grouped_member_is_delegated_to_the_member(
    session_factory: sessionmaker[Session],
) -> None:
    from incidentops.grouping import VERIFY_GROUP_MEMBERS_JOB
    from incidentops.persistence.models import AuditEventRow, OutboxJobRow

    action_id = await _verifying_action(session_factory)
    primary = _incident(session_factory, action_id)
    assert primary.group_id is not None
    member = _add_member(session_factory, primary, "GROUPED")

    outcome = await _errors_verifier(session_factory).verify(action_id)

    assert outcome is VerificationOutcome.PASSED
    assert _incident(session_factory, action_id).state == "RESOLVED"
    with session_factory() as session:
        record = session.scalars(select(VerificationRow)).one()
        checks = {c["check_id"]: c for c in record.checks}
        audit = session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == "verification_completed")
        ).one()
        member_job = session.scalars(
            select(OutboxJobRow).where(OutboxJobRow.kind == VERIFY_GROUP_MEMBERS_JOB)
        ).one()
    assert checks["api_errors_recovered"]["skipped"] is True
    assert checks["revision"]["passed"] is True
    assert audit.reason == "delegated to group member checks: api_errors_recovered"
    # The member is judged by its own alerts next (G4).
    assert member_job.payload["incident_id"] == member


@pytest.mark.parametrize("member_state", [None, "RECEIVED"], ids=["no_member", "split_member"])
async def test_check_on_another_service_still_counts_without_a_grouped_member(
    session_factory: sessionmaker[Session], member_state: str | None
) -> None:
    action_id = await _verifying_action(session_factory)
    if member_state is not None:
        # A member split out of the group is no longer vouched for by it.
        member = _add_member(session_factory, _incident(session_factory, action_id), member_state)
        with session_factory.begin() as session:
            row = session.get_one(IncidentRow, member)
            row.group_split_at = datetime.now().astimezone()

    outcome = await _errors_verifier(session_factory).verify(action_id)

    assert outcome is VerificationOutcome.VERIFICATION_FAILED
    with session_factory() as session:
        record = session.scalars(select(VerificationRow)).one()
    assert not any(c.get("skipped") for c in record.checks)
