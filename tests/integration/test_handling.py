"""Human handling of handed-over incidents, history, metrics and benchmarks APIs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from incidentops.api.app import create_app
from incidentops.auth.service import HumanRole
from incidentops.config import Settings
from incidentops.persistence.models import AuditEventRow, IncidentRow
from tests.integration.auth_helpers import sign_in
from tests.unit.test_alertmanager_normalize import payload
from tests.unit.tool_fakes import NOW, FakeBackend

pytestmark = pytest.mark.integration

TOKEN = "alertmanager-token-0123456789"


def _client(
    factory: sessionmaker[Session], backend: FakeBackend | None = None, bench: Path | None = None
) -> TestClient:
    settings = Settings(_env_file=None, alertmanager_token=SecretStr(TOKEN))
    return TestClient(
        create_app(
            settings,
            session_factory=factory,
            telemetry=backend or FakeBackend(),
            benchmark_dir=bench,
        )
    )


def _incident(client: TestClient, factory: sessionmaker[Session], state: str) -> str:
    response = client.post(
        "/api/v1/alerts/alertmanager",
        json=payload(),
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    (incident_id,) = response.json()["created_incident_ids"]
    with factory.begin() as session:
        session.execute(
            update(IncidentRow).where(IncidentRow.id == incident_id).values(state=state)
        )
    return str(incident_id)


def _version(factory: sessionmaker[Session], incident_id: str) -> int:
    with factory() as session:
        return session.get_one(IncidentRow, incident_id).version


def test_operator_assigns_then_closes_an_escalated_incident(
    session_factory: sessionmaker[Session],
) -> None:
    client = _client(session_factory)
    incident_id = _incident(client, session_factory, "ESCALATED")
    sign_in(client, session_factory, HumanRole.OPERATOR)

    listed = client.get("/api/v1/incidents").json()[0]
    assert listed["needs_human"] is True
    assert listed["title"]

    version = _version(session_factory, incident_id)
    assigned = client.post(
        f"/api/v1/incidents/{incident_id}/assign", json={"expected_version": version}
    )
    assert assigned.status_code == 200, assigned.text
    stale = client.post(
        f"/api/v1/incidents/{incident_id}/close",
        json={"expected_version": version, "disposition": "FIXED_MANUALLY"},
    )
    assert stale.status_code == 409
    closed = client.post(
        f"/api/v1/incidents/{incident_id}/close",
        json={
            "expected_version": assigned.json()["version"],
            "disposition": "FALSE_ALARM",
            "note": "Load test, not a real fault.",
        },
    )
    assert closed.status_code == 200, closed.text

    detail = client.get(f"/api/v1/incidents/{incident_id}").json()
    assert detail["state"] == "ESCALATED"
    assert detail["disposition"] == "FALSE_ALARM"
    assert detail["needs_human"] is False
    assert detail["owner_username"] == detail["disposed_by"]
    events = [e["event_type"] for e in detail["timeline"] if e["kind"] == "audit"]
    assert events[-2:] == ["incident_assigned", "incident_disposed"]

    again = client.post(
        f"/api/v1/incidents/{incident_id}/close",
        json={"expected_version": closed.json()["version"], "disposition": "WONT_FIX"},
    )
    assert again.status_code == 409
    assert again.json()["detail"]["outcome"] == "already_closed"


def test_viewers_cannot_close_and_active_incidents_cannot_be_closed(
    session_factory: sessionmaker[Session],
) -> None:
    client = _client(session_factory)
    escalated = _incident(client, session_factory, "ESCALATED")
    sign_in(client, session_factory, HumanRole.VIEWER)
    body = {"expected_version": _version(session_factory, escalated), "disposition": "WONT_FIX"}
    assert client.post(f"/api/v1/incidents/{escalated}/close", json=body).status_code == 403

    with session_factory.begin() as session:
        session.execute(
            update(IncidentRow).where(IncidentRow.id == escalated).values(state="INVESTIGATING")
        )
    sign_in(client, session_factory, HumanRole.OPERATOR)
    response = client.post(f"/api/v1/incidents/{escalated}/close", json=body)
    assert response.status_code == 409
    assert response.json()["detail"]["outcome"] == "not_handed_over"


def test_database_rejects_disposition_on_active_incident(
    session_factory: sessionmaker[Session],
) -> None:
    client = _client(session_factory)
    incident_id = _incident(client, session_factory, "INVESTIGATING")
    with pytest.raises(IntegrityError), session_factory.begin() as session:
        session.execute(
            update(IncidentRow)
            .where(IncidentRow.id == incident_id)
            .values(disposition="FALSE_ALARM", disposed_by="x", disposed_at=NOW)
        )


def test_recheck_is_read_only_and_audited(session_factory: sessionmaker[Session]) -> None:
    backend = FakeBackend()
    backend.lag = [(NOW.timestamp() - 60 + i * 10, 5.0) for i in range(6)]
    client = _client(session_factory, backend)
    incident_id = _incident(client, session_factory, "ESCALATED")
    version = _version(session_factory, incident_id)
    sign_in(client, session_factory, HumanRole.OPERATOR)

    response = client.post(f"/api/v1/incidents/{incident_id}/recheck")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "PASSED"
    assert [c["check_id"] for c in body["checks"]] == ["lag_recovered"]
    assert body["based_on"] == "service_defaults"
    assert _version(session_factory, incident_id) == version
    with session_factory() as session:
        audit = session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == "incident_rechecked")
        ).one()
        assert audit.decision == "PASSED"

    backend.fail = True
    assert client.post(f"/api/v1/incidents/{incident_id}/recheck").json()["outcome"] == "UNKNOWN"


def test_history_filters_and_stats(session_factory: sessionmaker[Session]) -> None:
    client = _client(session_factory)
    resolved = _incident(client, session_factory, "RESOLVED")
    with session_factory.begin() as session:
        session.execute(
            update(IncidentRow)
            .where(IncidentRow.id == resolved)
            .values(dedup_key="other-key", service_id="lab-api")
        )
    _incident(client, session_factory, "ESCALATED")
    sign_in(client, session_factory)

    page = client.get("/api/v1/history").json()
    assert page["total"] == 2
    assert page["stats"]["auto_resolved"] == 1
    assert page["stats"]["auto_resolved_pct"] == 50.0
    assert sorted(page["services"]) == ["lab-api", "persist-demo-consumer"]

    needs = client.get("/api/v1/history", params={"outcome": "needs_person"}).json()
    assert [r["outcome"] for r in needs["rows"]] == ["needs_person"]
    by_service = client.get("/api/v1/history", params={"q": "lab-api"}).json()
    assert [r["id"] for r in by_service["rows"]] == [resolved]
    paged = client.get("/api/v1/history", params={"page": 2, "page_size": 1}).json()
    assert len(paged["rows"]) == 1
    assert paged["total"] == 2


def test_metric_panels_use_the_allowlist(session_factory: sessionmaker[Session]) -> None:
    backend = FakeBackend()
    backend.overrides = {"lab_api:request_duration_seconds:p95_1m": [0.2, 0.9]}
    client = _client(session_factory, backend)
    sign_in(client, session_factory)

    assert client.get("/api/v1/metrics", params={"minutes": 7}).status_code == 422
    body = client.get("/api/v1/metrics", params={"minutes": 30}).json()
    panels = {p["panel_id"]: p for p in body["panels"]}
    assert set(panels) == {
        "api_p95",
        "api_5xx",
        "consumer_lag",
        "produce_consume",
        "pg_connections",
        "redis_memory",
        "api_cpu",
        "api_memory",
        "api_memory_growth",
        "redis_up",
        "pg_locks",
        "api_request_rate",
        "kafka_brokers",
        "pg_up",
        "pg_pool_usage",
    }
    assert panels["api_p95"]["status"] == "over"
    assert len(panels["api_request_rate"]["series"]) == 2
    assert panels["kafka_brokers"]["threshold_direction"] == "below"
    assert panels["api_5xx"]["status"] == "ok"
    assert len(panels["produce_consume"]["series"]) == 2
    assert body["grafana_dashboard_url"].endswith("/d/incidentops-lab")
    assert all("{{" not in q for q in backend.queries)

    backend.fail = True
    body = client.get("/api/v1/metrics", params={"minutes": 15}).json()
    assert {p["status"] for p in body["panels"]} == {"unavailable"}


def test_benchmark_runs_are_read_from_result_folders(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    run = tmp_path / "2026-11-10-B0-rules"
    run.mkdir()
    (run / "run-metadata.json").write_text(
        json.dumps(
            {
                "run_id": "r1",
                "variant": "B0_RULES",
                "model_id": "rules",
                "cases": ["B-F03"],
                "trials_per_case": 2,
                "started_at": "2026-11-10T20:00:00+00:00",
            }
        )
    )
    trial: dict[str, Any] = {
        "trial_id": "t1",
        "case_id": "B-F03",
        "status": "PASS",
        "time_to_detect_s": 40.0,
        "unauthorized_mutations_executed": 0,
        "prohibited_mutations_executed": 0,
    }
    (run / "trials.jsonl").write_text(
        json.dumps(trial) + "\n" + json.dumps({**trial, "trial_id": "t2", "status": "FAIL"}) + "\n"
    )
    (tmp_path / "incomplete").mkdir()
    client = _client(session_factory, bench=tmp_path)
    sign_in(client, session_factory)

    (body,) = client.get("/api/v1/benchmarks").json()
    assert (body["attempted"], body["passed"], body["unsafe_mutations"]) == (2, 1, 0)
    assert body["cases"][0]["passed"] == 1
    assert body["median_time_to_detect_s"] == 40.0


def test_published_b0_run_loads() -> None:
    from incidentops.api.benchmarks import list_runs

    runs = {r.folder: r for r in list_runs()}
    b0 = runs["2026-11-10-B0-rules"]
    assert (b0.passed, b0.attempted, b0.unsafe_mutations) == (32, 35, 0)


def test_incident_metrics_requires_session_and_returns_replay(
    session_factory: sessionmaker[Session],
) -> None:
    client = _client(session_factory)
    incident_id = _incident(client, session_factory, "ESCALATED")
    path = f"/api/v1/incidents/{incident_id}/metrics"
    assert client.get(path).status_code == 401
    sign_in(client, session_factory, HumanRole.VIEWER)
    response = client.get(path)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "historical"
    assert body["markers"][0]["label"] == "Alert started"
    assert len(body["panels"]) == 1
    assert client.get("/api/v1/incidents/inc_000000/metrics").status_code == 404


def test_metric_overlays_ignore_late_manual_edits_to_old_incidents(
    session_factory: sessionmaker[Session],
) -> None:
    from datetime import timedelta

    from incidentops.api.metrics import incident_windows
    from incidentops.domain.ids import new_id
    from incidentops.time import utcnow

    client = _client(session_factory)
    incident_id = _incident(client, session_factory, "ESCALATED")
    now = utcnow()
    ended = now - timedelta(days=1)
    with session_factory.begin() as session:
        session.execute(
            update(IncidentRow)
            .where(IncidentRow.id == incident_id)
            .values(created_at=ended - timedelta(minutes=5), updated_at=now)
        )
        session.add(
            AuditEventRow(
                id=new_id("aud"),
                actor="service:verifier",
                event_type="verification_completed",
                resource_type="incidents",
                resource_id=incident_id,
                decision="UNKNOWN",
                correlation_ids={"from_state": "VERIFYING", "to_state": "ESCALATED"},
                occurred_at=ended,
            )
        )
    with session_factory() as session:
        assert incident_windows(session, now - timedelta(minutes=30), now) == []
        old = incident_windows(
            session, ended - timedelta(minutes=10), ended + timedelta(minutes=10)
        )
        assert len(old) == 1
        assert old[0].end == ended
