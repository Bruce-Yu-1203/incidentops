"""Incident group fields in API responses and the manual ungroup (ADR-030, G5)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from incidentops.api.app import create_app
from incidentops.auth.service import HumanRole
from incidentops.config import Settings
from incidentops.domain.state_machines import IncidentState
from incidentops.persistence.models import AuditEventRow
from tests.integration.auth_helpers import sign_in
from tests.integration.test_grouping import incident, ingest, jobs

pytestmark = pytest.mark.integration


def test_incident_responses_carry_the_group(session_factory: sessionmaker[Session]) -> None:
    (postgres,) = ingest(session_factory, "lab_postgres_down")
    (api,) = ingest(session_factory, "lab_api_error_ratio_high")
    client = TestClient(create_app(Settings(_env_file=None), session_factory=session_factory))
    sign_in(client, session_factory)

    listed = {i["id"]: i for i in client.get("/api/v1/incidents").json()}
    assert listed[postgres]["primary_incident_id"] == postgres
    assert [m["id"] for m in listed[postgres]["members"]] == [api]
    assert listed[api]["primary_incident_id"] == postgres
    detail = client.get(f"/api/v1/incidents/{api}").json()
    assert detail["state"] == "GROUPED"
    assert detail["group_id"] == listed[postgres]["group_id"]


def test_ungroup_endpoint_splits_a_member(session_factory: sessionmaker[Session]) -> None:
    (postgres,) = ingest(session_factory, "lab_postgres_down")
    (api,) = ingest(session_factory, "lab_api_error_ratio_high")
    version = incident(session_factory, api).version
    client = TestClient(create_app(Settings(_env_file=None), session_factory=session_factory))
    body = {"expected_version": version, "reason": "separate bad release"}

    assert client.post(f"/api/v1/incidents/{api}/ungroup", json=body).status_code == 401
    sign_in(client, session_factory, HumanRole.VIEWER)
    assert client.post(f"/api/v1/incidents/{api}/ungroup", json=body).status_code == 403
    sign_in(client, session_factory, HumanRole.OPERATOR)
    stale = client.post(f"/api/v1/incidents/{api}/ungroup", json={"expected_version": 99})
    assert stale.status_code == 409
    response = client.post(f"/api/v1/incidents/{api}/ungroup", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "ungrouped"

    row = incident(session_factory, api)
    assert row.state == IncidentState.RECEIVED
    assert row.group_split_at is not None
    assert len(jobs(session_factory, api)) == 1
    with session_factory() as session:
        audit = session.scalars(
            select(AuditEventRow).where(
                AuditEventRow.resource_id == api,
                AuditEventRow.event_type == "incident_group_split",
            )
        ).one()
    assert audit.actor.startswith("user:operator-")
    assert "separate bad release" in (audit.reason or "")
    # Neither the primary nor an already split member can be ungrouped.
    for target in (postgres, api):
        again = client.post(
            f"/api/v1/incidents/{target}/ungroup",
            json={"expected_version": incident(session_factory, target).version},
        )
        assert again.status_code == 409
    # A split member no longer counts as an active member of the group.
    detail = client.get(f"/api/v1/incidents/{postgres}").json()
    assert detail["members"] == [
        {"id": api, "service_id": "lab-api", "state": "RECEIVED", "split": True}
    ]
