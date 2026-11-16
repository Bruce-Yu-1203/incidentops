"""Incident groups across related services (ADR-030) against real PostgreSQL."""

from __future__ import annotations

import hashlib
import threading
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from incidentops.actions.proposals import ProposalEvaluator
from incidentops.agent.model import ModelTurn, ScriptedModelAdapter
from incidentops.api.app import create_app
from incidentops.auth.service import HumanRole
from incidentops.config import Settings
from incidentops.domain.catalog import ServiceCatalog, load_catalog
from incidentops.domain.schemas import AlertEvent
from incidentops.domain.state_machines import IncidentState
from incidentops.grouping import (
    INTRINSIC_ON_MEMBER_NOTE,
    INVESTIGATE_JOB,
    SPLIT_BY_VERIFIER_NOTE,
    UNEXPLAINED_NOTE,
    VERIFY_GROUP_MEMBERS_JOB,
    GroupingConfig,
)
from incidentops.ingestion.alertmanager import normalize
from incidentops.ingestion.service import ingest_events
from incidentops.persistence.models import (
    ActionProposalRow,
    AuditEventRow,
    DiagnosisRow,
    IncidentGroupRow,
    IncidentRow,
    OutboxJobRow,
)
from incidentops.persistence.transitions import AuditSpec, transition
from incidentops.policy.engine import PolicyEngine, load_policy
from incidentops.time import utcnow
from incidentops.verification.verifier import RecoveryVerifier, load_verify_config
from incidentops.workers.investigation import process_one
from tests.integration.auth_helpers import sign_in
from tests.integration.helpers import FakeInspector, diagnosed_incident
from tests.integration.test_investigation_worker import OWNER, _runner
from tests.unit.test_investigation_graph import lag_and_status, submit
from tests.unit.tool_fakes import FakeBackend

pytestmark = pytest.mark.integration

ACTOR = "service:alertmanager"
CONFIG = GroupingConfig(load_catalog(), window_seconds=180, settle_seconds=60)
NO_SETTLE = GroupingConfig(load_catalog(), window_seconds=180, settle_seconds=0)

# alertname -> (service_id, symptom_class), as in infra/prometheus/rules/lab.rules.yml.
RULES = {
    "lab_postgres_down": ("lab-postgres", "intrinsic"),
    "lab_kafka_broker_unreachable": ("lab-kafka", "intrinsic"),
    "lab_api_error_ratio_high": ("lab-api", "dependency"),
    "lab_api_latency_p95_high": ("lab-api", "dependency"),
    "lab_api_memory_growth": ("lab-api", "intrinsic"),
    "lab_api_cpu_high": ("lab-api", "intrinsic"),
    "lab_kafka_consumer_lag_high": ("persist-demo-consumer", "dependency"),
}


def event(alertname: str, status: str = "firing", **labels: str) -> AlertEvent:
    service_id, symptom = RULES.get(alertname, (labels.get("service_id", ""), "intrinsic"))
    merged = {
        "alertname": alertname,
        "service_id": service_id,
        "environment": "fault-lab",
        "severity": "critical",
        "symptom_class": symptom,
        **labels,
    }
    fingerprint = hashlib.sha256(alertname.encode()).hexdigest()[:16]
    raw = {
        "version": "4",
        "status": status,
        "receiver": "incidentops",
        "alerts": [
            {
                "status": status,
                "labels": merged,
                "annotations": {"summary": alertname},
                "startsAt": "2026-11-15T05:00:00Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "fingerprint": fingerprint,
            }
        ],
    }
    return normalize(raw, utcnow())[0]


def ingest(
    factory: sessionmaker[Session], *alertnames: str, config: GroupingConfig | None = CONFIG
) -> list[str]:
    result = ingest_events(factory, [event(a) for a in alertnames], ACTOR, grouping=config)
    return result.created_incident_ids


def incident(factory: sessionmaker[Session], incident_id: str) -> IncidentRow:
    with factory() as session:
        return session.get_one(IncidentRow, incident_id)


def jobs(
    factory: sessionmaker[Session], incident_id: str, kind: str = INVESTIGATE_JOB
) -> list[Any]:
    with factory() as session:
        return list(
            session.scalars(
                select(OutboxJobRow).where(
                    OutboxJobRow.kind == kind, OutboxJobRow.aggregate_id == incident_id
                )
            )
        )


def audits(factory: sessionmaker[Session], incident_id: str) -> list[str]:
    with factory() as session:
        return list(
            session.scalars(
                select(AuditEventRow.event_type)
                .where(AuditEventRow.resource_id == incident_id)
                .order_by(AuditEventRow.seq)
            )
        )


def primary_of(factory: sessionmaker[Session], incident_id: str) -> str:
    with factory() as session:
        row = session.get_one(IncidentRow, incident_id)
        assert row.group_id is not None
        return session.get_one(IncidentGroupRow, row.group_id).primary_incident_id


def drive(factory: sessionmaker[Session], incident_id: str, *states: IncidentState) -> None:
    """Walk an incident through ``states`` with guarded transitions."""
    for target in states:
        with factory.begin() as session:
            row = session.get_one(IncidentRow, incident_id)
            transition(
                session,
                IncidentRow,
                incident_id,
                expected_state=IncidentState(row.state),
                expected_version=row.version,
                target_state=target,
                audit=AuditSpec(actor="service:test", event_type="test_transition"),
            )


TO_VERIFYING = (
    IncidentState.TRIAGED,
    IncidentState.INVESTIGATING,
    IncidentState.DIAGNOSED,
    IncidentState.AWAITING_APPROVAL,
    IncidentState.EXECUTING,
    IncidentState.VERIFYING,
)


def verifier(factory: sessionmaker[Session], wait_seconds: int = 0) -> RecoveryVerifier:
    catalog = load_catalog()

    async def no_sleep(_: float) -> None:
        return None

    return RecoveryVerifier(
        factory,
        catalog,
        FakeBackend(),
        FakeInspector(),
        load_verify_config(),
        sleep=no_sleep,
        member_recovery_seconds=wait_seconds,
    )


# -- joining ------------------------------------------------------------------------


def test_dependency_symptom_joins_the_upstream_group(
    session_factory: sessionmaker[Session],
) -> None:
    (postgres,) = ingest(session_factory, "lab_postgres_down")
    (api,) = ingest(session_factory, "lab_api_error_ratio_high")

    assert incident(session_factory, api).state == IncidentState.GROUPED
    assert incident(session_factory, api).group_id == incident(session_factory, postgres).group_id
    assert primary_of(session_factory, api) == postgres
    assert jobs(session_factory, api) == []
    assert "incident_grouped" in audits(session_factory, api)
    # Most upstream: investigated immediately, no settle wait.
    (job,) = jobs(session_factory, postgres)
    with session_factory() as session:
        assert job.available_at <= session.scalar(select(func.now()))


def test_later_alerts_of_a_grouped_service_append_to_it(
    session_factory: sessionmaker[Session],
) -> None:
    ingest(session_factory, "lab_postgres_down")
    (api,) = ingest(session_factory, "lab_api_error_ratio_high")
    result = ingest_events(
        session_factory, [event("lab_api_latency_p95_high")], ACTOR, grouping=CONFIG
    )
    assert result.created_incident_ids == []
    assert result.updated_incident_ids == [api]


def test_intrinsic_symptom_never_joins_as_member(session_factory: sessionmaker[Session]) -> None:
    (postgres,) = ingest(session_factory, "lab_postgres_down")
    (api,) = ingest(session_factory, "lab_api_memory_growth")

    row = incident(session_factory, api)
    assert row.state == IncidentState.RECEIVED
    assert row.group_id != incident(session_factory, postgres).group_id
    assert primary_of(session_factory, api) == api
    assert len(jobs(session_factory, api)) == 1
    assert "incident_not_grouped" in audits(session_factory, api)


def test_window_expiry_starts_a_new_group(session_factory: sessionmaker[Session]) -> None:
    (postgres,) = ingest(session_factory, "lab_postgres_down")
    with session_factory.begin() as session:
        session.execute(
            update(IncidentRow)
            .where(IncidentRow.id == postgres)
            .values(created_at=func.now() - timedelta(seconds=CONFIG.window_seconds + 1))
        )
    (api,) = ingest(session_factory, "lab_api_error_ratio_high")
    assert incident(session_factory, api).state == IncidentState.RECEIVED
    assert primary_of(session_factory, api) == api


def test_resolved_incident_never_attracts_members(session_factory: sessionmaker[Session]) -> None:
    (postgres,) = ingest(session_factory, "lab_postgres_down")
    drive(session_factory, postgres, IncidentState.FAILED)
    (api,) = ingest(session_factory, "lab_api_error_ratio_high")
    assert incident(session_factory, api).state == IncidentState.RECEIVED
    assert primary_of(session_factory, api) == api


def test_unrelated_services_are_not_grouped(session_factory: sessionmaker[Session]) -> None:
    (api,) = ingest(session_factory, "lab_api_error_ratio_high")
    (consumer,) = ingest(session_factory, "lab_kafka_consumer_lag_high")
    assert incident(session_factory, consumer).state == IncidentState.RECEIVED
    assert primary_of(session_factory, consumer) == consumer
    assert primary_of(session_factory, api) == api


def test_without_grouping_config_incidents_stay_alone(
    session_factory: sessionmaker[Session],
) -> None:
    ingest(session_factory, "lab_postgres_down", config=None)
    (api,) = ingest(session_factory, "lab_api_error_ratio_high", config=None)
    row = incident(session_factory, api)
    assert (row.state, row.group_id) == (IncidentState.RECEIVED, None)
    assert len(jobs(session_factory, api)) == 1


# -- settle wait and re-primarying --------------------------------------------------------


def test_downstream_primary_waits_the_settle_time(session_factory: sessionmaker[Session]) -> None:
    (api,) = ingest(session_factory, "lab_api_error_ratio_high")
    (job,) = jobs(session_factory, api)
    with session_factory() as session:
        now = session.scalar(select(func.now()))
    assert now is not None
    delay = (job.available_at - now).total_seconds()
    assert CONFIG.settle_seconds - 5 <= delay <= CONFIG.settle_seconds


def test_upstream_newcomer_takes_over_before_the_run_starts(
    session_factory: sessionmaker[Session],
) -> None:
    (api,) = ingest(session_factory, "lab_api_error_ratio_high")
    (postgres,) = ingest(session_factory, "lab_postgres_down")

    assert primary_of(session_factory, api) == postgres
    assert incident(session_factory, api).state == IncidentState.GROUPED
    (cancelled,) = jobs(session_factory, api)
    assert cancelled.status == "DONE"
    assert (cancelled.last_error or "").startswith("cancelled:")
    assert len(jobs(session_factory, postgres)) == 1
    assert "group_primary_changed" in audits(session_factory, postgres)


def test_intrinsic_primary_is_never_replaced(session_factory: sessionmaker[Session]) -> None:
    # A consumer incident opened by an intrinsic symptom keeps its own group.
    result = ingest_events(
        session_factory,
        [event("lab_consumer_stalled", service_id="persist-demo-consumer")],
        ACTOR,
        grouping=CONFIG,
    )
    (consumer,) = result.created_incident_ids
    (kafka,) = ingest(session_factory, "lab_kafka_broker_unreachable")
    assert primary_of(session_factory, consumer) == consumer
    assert primary_of(session_factory, kafka) == kafka
    assert "incident_not_grouped" in audits(session_factory, kafka)


async def test_upstream_newcomer_during_the_run_drops_the_old_proposal(
    session_factory: sessionmaker[Session],
) -> None:
    (consumer,) = ingest(session_factory, "lab_kafka_consumer_lag_high", config=NO_SETTLE)
    kafka: list[str] = []

    def kafka_alerts_mid_run(messages: list[dict[str, Any]]) -> ModelTurn:
        kafka.extend(ingest(session_factory, "lab_kafka_broker_unreachable", config=NO_SETTLE))
        return lag_and_status()

    model = ScriptedModelAdapter([kafka_alerts_mid_run, submit])
    await process_one(session_factory, _runner(session_factory, model), OWNER, 60)

    row = incident(session_factory, consumer)
    assert row.state == IncidentState.GROUPED
    assert primary_of(session_factory, consumer) == kafka[0]
    with session_factory() as session:
        assert session.scalars(select(DiagnosisRow)).one() is not None  # kept as context
        evaluations = session.scalars(
            select(OutboxJobRow).where(OutboxJobRow.kind == "evaluate_action_proposal")
        ).all()
    assert evaluations == []
    assert "group_primary_replaced" in audits(session_factory, consumer)
    assert len(jobs(session_factory, kafka[0])) == 1


async def test_primary_context_lists_the_group_members(
    session_factory: sessionmaker[Session],
) -> None:
    (consumer,) = ingest(session_factory, "lab_kafka_consumer_lag_high", config=NO_SETTLE)
    with session_factory.begin() as session:
        session.execute(
            update(IncidentRow)
            .where(IncidentRow.id == consumer)
            .values(symptom_class="intrinsic")  # keep it primary
        )
    custom = _catalog_with("demo-producer", ["lab-kafka", "persist-demo-consumer"])
    producer_event = event(
        "lab_producer_send_errors", service_id="demo-producer", symptom_class="dependency"
    )
    ingest_events(session_factory, [producer_event], ACTOR, grouping=GroupingConfig(custom))
    model = ScriptedModelAdapter([lag_and_status(), submit])
    await process_one(session_factory, _runner(session_factory, model), OWNER, 60)

    first_message = model.calls[0][0]["content"][0]["text"]
    assert '"incident_group"' in first_message
    assert "demo-producer" in first_message
    assert "lab_producer_send_errors" in first_message


# -- concurrency ----------------------------------------------------------------------


def test_two_deliveries_racing_form_one_group(session_factory: sessionmaker[Session]) -> None:
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def deliver(alertname: str) -> None:
        try:
            barrier.wait()
            ingest(session_factory, alertname)
        except BaseException as exc:  # surfaced below
            errors.append(exc)

    threads = [
        threading.Thread(target=deliver, args=(name,))
        for name in ("lab_postgres_down", "lab_api_error_ratio_high")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    with session_factory() as session:
        rows = {r.service_id: r for r in session.scalars(select(IncidentRow))}
        pending = session.scalars(
            select(OutboxJobRow.aggregate_id).where(
                OutboxJobRow.kind == INVESTIGATE_JOB, OutboxJobRow.status == "PENDING"
            )
        ).all()
    assert rows["lab-api"].group_id == rows["lab-postgres"].group_id
    assert primary_of(session_factory, rows["lab-api"].id) == rows["lab-postgres"].id
    assert rows["lab-api"].state == IncidentState.GROUPED
    assert pending == [rows["lab-postgres"].id]


# -- following the primary ---------------------------------------------------------------


async def test_verified_primary_resolves_recovered_members_and_splits_the_rest(
    session_factory: sessionmaker[Session],
) -> None:
    (postgres,) = ingest(session_factory, "lab_postgres_down")
    (api,) = ingest(session_factory, "lab_api_error_ratio_high")
    (consumer,) = ingest(session_factory, "lab_kafka_consumer_lag_high")
    assert incident(session_factory, consumer).state == IncidentState.GROUPED

    drive(session_factory, postgres, *TO_VERIFYING, IncidentState.RESOLVED)
    assert len(jobs(session_factory, api, VERIFY_GROUP_MEMBERS_JOB)) == 1
    assert len(jobs(session_factory, consumer, VERIFY_GROUP_MEMBERS_JOB)) == 1

    ingest_events(
        session_factory, [event("lab_api_error_ratio_high", "resolved")], ACTOR, grouping=CONFIG
    )
    check = verifier(session_factory)
    assert await check.verify_member(api, postgres) is IncidentState.RESOLVED
    assert await check.verify_member(consumer, postgres) is IncidentState.RECEIVED
    # At-least-once: a repeated job changes nothing.
    assert await check.verify_member(api, postgres) is None

    assert "group_member_resolved" in audits(session_factory, api)
    split = incident(session_factory, consumer)
    assert split.group_split_at is not None
    assert split.group_id == incident(session_factory, postgres).group_id  # history kept
    assert split.group_note == SPLIT_BY_VERIFIER_NOTE
    (job,) = jobs(session_factory, consumer)
    assert job.payload["note"] == SPLIT_BY_VERIFIER_NOTE


async def test_split_member_is_investigated_with_the_note(
    session_factory: sessionmaker[Session],
) -> None:
    (kafka,) = ingest(session_factory, "lab_kafka_broker_unreachable", config=NO_SETTLE)
    (consumer,) = ingest(session_factory, "lab_kafka_consumer_lag_high", config=NO_SETTLE)
    drive(session_factory, kafka, *TO_VERIFYING, IncidentState.RESOLVED)
    assert await verifier(session_factory).verify_member(consumer, kafka) is IncidentState.RECEIVED
    with session_factory.begin() as session:
        # The primary's own investigation job is not part of this test.
        session.execute(
            update(OutboxJobRow).where(OutboxJobRow.aggregate_id == kafka).values(status="DONE")
        )
    model = ScriptedModelAdapter([lag_and_status(), submit])
    await process_one(session_factory, _runner(session_factory, model), OWNER, 60)
    first_message = model.calls[0][0]["content"][0]["text"]
    assert SPLIT_BY_VERIFIER_NOTE in first_message
    assert incident(session_factory, consumer).state == IncidentState.DIAGNOSED


async def test_member_wait_resolves_when_alert_clears_in_time(
    session_factory: sessionmaker[Session],
) -> None:
    (postgres,) = ingest(session_factory, "lab_postgres_down")
    (api,) = ingest(session_factory, "lab_api_error_ratio_high")
    drive(session_factory, postgres, *TO_VERIFYING, IncidentState.RESOLVED)
    sleeps: list[float] = []

    async def resolve_on_first_poll(seconds: float) -> None:
        sleeps.append(seconds)
        ingest_events(
            session_factory,
            [event("lab_api_error_ratio_high", "resolved")],
            ACTOR,
            grouping=CONFIG,
        )

    check = RecoveryVerifier(
        session_factory,
        load_catalog(),
        FakeBackend(),
        FakeInspector(),
        load_verify_config(),
        sleep=resolve_on_first_poll,
        member_recovery_seconds=600,
    )
    assert await check.verify_member(api, postgres) is IncidentState.RESOLVED
    assert len(sleeps) == 1


@pytest.mark.parametrize(
    "path",
    [
        (IncidentState.FAILED,),
        (
            IncidentState.TRIAGED,
            IncidentState.INVESTIGATING,
            IncidentState.DIAGNOSED,
            IncidentState.INSUFFICIENT_EVIDENCE,
            IncidentState.ESCALATED,
        ),
        (IncidentState.TRIAGED, IncidentState.INVESTIGATING, IncidentState.DIAGNOSED,
         IncidentState.DIAGNOSED_ONLY),
    ],
    ids=["failed", "escalated", "diagnosed_only"],
)  # fmt: skip
def test_members_follow_a_handed_over_primary_to_escalated(
    session_factory: sessionmaker[Session], path: tuple[IncidentState, ...]
) -> None:
    (postgres,) = ingest(session_factory, "lab_postgres_down")
    (api,) = ingest(session_factory, "lab_api_error_ratio_high")
    drive(session_factory, postgres, *path)
    row = incident(session_factory, api)
    assert row.state == IncidentState.ESCALATED
    assert row.escalation_reason == "group_primary_escalated"


def test_cancelled_primary_releases_its_members(session_factory: sessionmaker[Session]) -> None:
    (postgres,) = ingest(session_factory, "lab_postgres_down")
    (api,) = ingest(session_factory, "lab_api_error_ratio_high")
    drive(session_factory, postgres, IncidentState.CANCELLED)
    row = incident(session_factory, api)
    assert row.state == IncidentState.RECEIVED
    assert row.group_split_at is not None
    assert len(jobs(session_factory, api)) == 1


# -- approvals and the API ---------------------------------------------------------------


def _catalog_with(service_id: str, depends_on: list[str]) -> ServiceCatalog:
    raw = load_catalog().model_dump(mode="json", exclude_none=True)
    for entry in raw["services"]:
        if entry["service_id"] == service_id:
            entry["depends_on"] = depends_on
    return ServiceCatalog.model_validate(raw)


async def test_member_joining_does_not_invalidate_an_approval(
    session_factory: sessionmaker[Session],
) -> None:
    incident_id, diagnosis_id = await diagnosed_incident(session_factory)
    evaluator = ProposalEvaluator(
        session_factory, PolicyEngine(load_policy(), load_catalog()), FakeInspector()
    )
    outcome = await evaluator.evaluate(incident_id, diagnosis_id)
    with session_factory() as session:
        action = session.get_one(ActionProposalRow, outcome.action_id)
        before = (action.action_hash, action.version)

    # A test catalog makes demo-producer downstream of the consumer, so a
    # producer alert joins the consumer's group while approval is pending.
    custom = GroupingConfig(_catalog_with("demo-producer", ["lab-kafka", "persist-demo-consumer"]))
    producer = event(
        "lab_producer_send_errors", service_id="demo-producer", symptom_class="dependency"
    )
    (member,) = ingest_events(
        session_factory, [producer], ACTOR, grouping=custom
    ).created_incident_ids
    assert primary_of(session_factory, member) == incident_id

    client = TestClient(create_app(Settings(_env_file=None), session_factory=session_factory))
    headers = sign_in(client, session_factory, HumanRole.APPROVER)
    response = client.post(
        f"/api/v1/actions/{outcome.action_id}/approve",
        json={"action_hash": before[0], "expected_version": before[1], "reason": "ok"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "approved"


# -- G2b ---------------------------------------------------------------------------


def test_intrinsic_alert_on_a_member_splits_it_out(session_factory: sessionmaker[Session]) -> None:
    (postgres,) = ingest(session_factory, "lab_postgres_down")
    (api,) = ingest(session_factory, "lab_api_error_ratio_high")
    assert incident(session_factory, api).state == IncidentState.GROUPED

    result = ingest_events(session_factory, [event("lab_api_cpu_high")], ACTOR, grouping=CONFIG)
    assert result.updated_incident_ids == [api]
    row = incident(session_factory, api)
    assert row.state == IncidentState.RECEIVED
    assert row.group_split_at is not None
    (job,) = jobs(session_factory, api)
    assert job.payload["note"] == INTRINSIC_ON_MEMBER_NOTE
    with session_factory() as session:
        reason = session.scalars(
            select(AuditEventRow.reason).where(
                AuditEventRow.resource_id == api,
                AuditEventRow.event_type == "incident_group_split",
            )
        ).one()
    assert (reason or "").startswith("intrinsic_symptom_on_member")
    assert incident(session_factory, postgres).state == IncidentState.RECEIVED  # unchanged


def test_dependency_or_resolved_alerts_keep_a_member_grouped(
    session_factory: sessionmaker[Session],
) -> None:
    ingest(session_factory, "lab_postgres_down")
    (api,) = ingest(session_factory, "lab_api_error_ratio_high")
    ingest_events(
        session_factory,
        [event("lab_api_latency_p95_high"), event("lab_api_cpu_high", "resolved")],
        ACTOR,
        grouping=CONFIG,
    )
    assert incident(session_factory, api).state == IncidentState.GROUPED
    assert jobs(session_factory, api) == []


# -- G3 ------------------------------------------------------------------------------


async def test_primary_splits_out_the_members_it_cannot_explain(
    session_factory: sessionmaker[Session],
) -> None:
    (consumer,) = ingest(session_factory, "lab_kafka_consumer_lag_high", config=NO_SETTLE)
    with session_factory.begin() as session:
        session.execute(
            update(IncidentRow).where(IncidentRow.id == consumer).values(symptom_class="intrinsic")
        )
    custom = GroupingConfig(_catalog_with("demo-producer", ["lab-kafka", "persist-demo-consumer"]))
    producer_event = event(
        "lab_producer_send_errors", service_id="demo-producer", symptom_class="dependency"
    )
    (member,) = ingest_events(
        session_factory, [producer_event], ACTOR, grouping=custom
    ).created_incident_ids
    bogus = "inc_" + "0" * 32

    def submit_with_unexplained(messages: list[dict[str, Any]]) -> ModelTurn:
        turn = submit(messages)
        turn.tool_calls[0].arguments["unexplained_incident_ids"] = [member, bogus, member]
        return turn

    model = ScriptedModelAdapter([lag_and_status(), submit_with_unexplained])
    await process_one(session_factory, _runner(session_factory, model), OWNER, 60)

    first_message = model.calls[0][0]["content"][0]["text"]
    assert f'"incident_id": "{member}"' in first_message
    row = incident(session_factory, member)
    assert row.state == IncidentState.RECEIVED
    assert row.group_note == UNEXPLAINED_NOTE
    assert len(jobs(session_factory, member)) == 1
    assert incident(session_factory, consumer).state == IncidentState.DIAGNOSED
    with session_factory() as session:
        review = session.scalars(
            select(AuditEventRow.reason).where(
                AuditEventRow.event_type == "unexplained_members_reviewed"
            )
        ).one()
    assert review == "1 split, 1 dropped (not current group members)"
