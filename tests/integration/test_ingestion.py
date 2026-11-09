"""Alert ingestion, deduplication races and incident read API on real PostgreSQL."""

from __future__ import annotations

import threading
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from incidentops.api.app import create_app
from incidentops.config import Settings
from incidentops.domain.state_machines import IncidentState
from incidentops.ingestion.service import INVESTIGATE_JOB
from incidentops.persistence.models import AlertEventRow, AuditEventRow, IncidentRow, OutboxJobRow
from incidentops.persistence.transitions import AuditSpec, transition
from tests.unit.test_alertmanager_normalize import payload

pytestmark = pytest.mark.integration

TOKEN = "alertmanager-token-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _client(factory: sessionmaker[Session]) -> TestClient:
    settings = Settings(_env_file=None, alertmanager_token=SecretStr(TOKEN))
    return TestClient(create_app(settings, session_factory=factory))


def _count(factory: sessionmaker[Session], model: Any) -> int:
    with factory() as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def _post(client: TestClient, body: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/api/v1/alerts/alertmanager", json=body, headers=AUTH)
    assert response.status_code == 202, response.text
    return dict(response.json())


def test_first_alert_creates_incident_audit_and_one_job(
    session_factory: sessionmaker[Session],
) -> None:
    body = _post(_client(session_factory), payload())
    (incident_id,) = body["created_incident_ids"]
    with session_factory() as session:
        incident = session.get_one(IncidentRow, incident_id)
        assert (incident.state, incident.version) == ("RECEIVED", 1)
        job = session.scalars(select(OutboxJobRow)).one()
        assert (job.kind, job.aggregate_id) == (INVESTIGATE_JOB, incident_id)
        audit = session.scalars(select(AuditEventRow)).one()
        assert audit.event_type == "incident_created"


def test_repeated_deliveries_retain_events_without_new_incident(
    session_factory: sessionmaker[Session],
) -> None:
    # R-01: duplicate webhooks map to one active incident; history is kept.
    client = _client(session_factory)
    first = _post(client, payload())
    for _ in range(3):
        again = _post(client, payload())
        assert again["created_incident_ids"] == []
        assert again["updated_incident_ids"] == first["created_incident_ids"]
    assert _count(session_factory, IncidentRow) == 1
    assert _count(session_factory, AlertEventRow) == 4
    assert _count(session_factory, OutboxJobRow) == 1


def test_concurrent_deliveries_create_exactly_one_incident(
    session_factory: sessionmaker[Session],
) -> None:
    client = _client(session_factory)
    barrier = threading.Barrier(8)
    errors: list[BaseException] = []

    def deliver() -> None:
        try:
            barrier.wait()
            _post(client, payload())
        except BaseException as exc:  # surface failures from worker threads
            errors.append(exc)

    threads = [threading.Thread(target=deliver) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert _count(session_factory, IncidentRow) == 1
    assert _count(session_factory, AlertEventRow) == 8
    assert _count(session_factory, OutboxJobRow) == 1


def test_different_rules_create_separate_incidents(session_factory: sessionmaker[Session]) -> None:
    client = _client(session_factory)
    lag = payload()
    latency = payload(
        labels={
            "alertname": "lab_api_latency_p95_high",
            "service_id": "lab-api",
            "environment": "fault-lab",
            "severity": "warning",
        },
        fingerprint="ffee0011ffee0011",
    )
    combined = dict(lag)
    combined["alerts"] = lag["alerts"] + latency["alerts"]
    body = _post(client, combined)
    assert len(body["created_incident_ids"]) == 2


def test_resolved_alert_never_resolves_incident(session_factory: sessionmaker[Session]) -> None:
    client = _client(session_factory)
    (incident_id,) = _post(client, payload())["created_incident_ids"]
    resolved = _post(client, payload(status="resolved", endsAt="2026-11-09T12:05:00Z"))
    assert resolved["updated_incident_ids"] == [incident_id]
    with session_factory() as session:
        assert session.get_one(IncidentRow, incident_id).state == "RECEIVED"


def test_resolved_alert_without_incident_is_ignored(session_factory: sessionmaker[Session]) -> None:
    body = _post(_client(session_factory), payload(status="resolved"))
    assert body["ignored_resolved"] == 1
    assert _count(session_factory, IncidentRow) == 0


def test_new_incident_after_previous_reaches_terminal_state(
    session_factory: sessionmaker[Session],
) -> None:
    client = _client(session_factory)
    (first,) = _post(client, payload())["created_incident_ids"]
    with session_factory.begin() as session:
        transition(
            session,
            IncidentRow,
            first,
            expected_state=IncidentState.RECEIVED,
            expected_version=1,
            target_state=IncidentState.CANCELLED,
            audit=AuditSpec(actor="test", event_type="cancelled"),
        )
    (second,) = _post(client, payload())["created_incident_ids"]
    assert second != first


def test_list_and_detail_endpoints(session_factory: sessionmaker[Session]) -> None:
    client = _client(session_factory)
    (incident_id,) = _post(client, payload())["created_incident_ids"]
    _post(client, payload())

    listed = client.get("/api/v1/incidents").json()
    assert [item["id"] for item in listed] == [incident_id]
    assert client.get("/api/v1/incidents", params={"state": "RESOLVED"}).json() == []
    assert client.get("/api/v1/incidents", params={"service_id": "lab-api"}).json() == []

    detail = client.get(f"/api/v1/incidents/{incident_id}").json()
    assert detail["state"] == "RECEIVED"
    assert len(detail["alert_events"]) == 2
    assert detail["alert_events"][0]["labels"]["component"] == "kafka-consumer"
    kinds = [entry["kind"] for entry in detail["timeline"]]
    assert kinds.count("alert") == 2
    assert "audit" in kinds
    assert client.get("/api/v1/incidents/inc_missing").status_code == 404


def test_incident_history_survives_api_restart(session_factory: sessionmaker[Session]) -> None:
    (incident_id,) = _post(_client(session_factory), payload())["created_incident_ids"]
    restarted = _client(session_factory)  # a fresh application instance
    assert restarted.get(f"/api/v1/incidents/{incident_id}").status_code == 200
