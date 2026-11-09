"""Authentication, RBAC and approval decisions over HTTP (S-03..S-06, S-10)."""

from __future__ import annotations

import threading
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from incidentops.actions.approvals import EXECUTE_ACTION_JOB, expire_due
from incidentops.actions.proposals import ProposalEvaluator
from incidentops.api.app import create_app
from incidentops.auth.service import HumanRole, create_user
from incidentops.config import Settings
from incidentops.domain.catalog import load_catalog
from incidentops.persistence.models import (
    ActionProposalRow,
    ApprovalDecisionRow,
    ApprovalRequestRow,
    AuditEventRow,
    IncidentRow,
    OutboxJobRow,
    SessionRow,
)
from incidentops.policy.engine import PolicyEngine, load_policy
from incidentops.time import utcnow
from tests.integration.helpers import FakeInspector, diagnosed_incident

pytestmark = pytest.mark.integration

PASSWORD = "lab-password-0123"


def _seed(factory: sessionmaker[Session]) -> None:
    with factory.begin() as session:
        for role in HumanRole:
            create_user(session, role.value, PASSWORD, role)


def _client(factory: sessionmaker[Session]) -> TestClient:
    return TestClient(create_app(Settings(_env_file=None), session_factory=factory))


def _login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.cookies.clear()  # use explicit headers so tests can switch users
    return {"Authorization": f"Session {response.headers['X-Session-Token']}"}


async def _pending_action(factory: sessionmaker[Session]) -> dict[str, Any]:
    incident_id, diagnosis_id = await diagnosed_incident(factory)
    evaluator = ProposalEvaluator(
        factory, PolicyEngine(load_policy(), load_catalog()), FakeInspector()
    )
    outcome = await evaluator.evaluate(incident_id, diagnosis_id)
    with factory() as session:
        action = session.get_one(ActionProposalRow, outcome.action_id)
        return {
            "id": action.id,
            "hash": action.action_hash,
            "version": action.version,
            "incident_id": incident_id,
        }


def _count(factory: sessionmaker[Session], model: Any, *where: Any) -> int:
    with factory() as session:
        return session.scalar(select(func.count()).select_from(model).where(*where)) or 0


def _approve(
    client: TestClient, auth: dict[str, str], action: dict[str, Any], **override: Any
) -> Any:
    body = {"action_hash": action["hash"], "expected_version": action["version"], **override}
    return client.post(f"/api/v1/actions/{action['id']}/approve", json=body, headers=auth)


def test_login_logout_and_failed_attempts(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)
    client = _client(session_factory)
    bad = client.post("/api/v1/auth/login", json={"username": "approver", "password": "nope"})
    assert bad.status_code == 401
    ok = client.post("/api/v1/auth/login", json={"username": "approver", "password": PASSWORD})
    assert ok.status_code == 200
    cookie = ok.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert client.get("/api/v1/auth/me").json() == {"username": "approver", "role": "approver"}
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401
    with session_factory() as session:
        outcomes = [
            e.decision
            for e in session.scalars(
                select(AuditEventRow)
                .where(AuditEventRow.event_type == "login")
                .order_by(AuditEventRow.seq)
            )
        ]
        token_hashes = session.scalars(select(SessionRow.token_hash)).all()
    assert outcomes == ["failure", "success"]
    assert all(len(h) == 64 for h in token_hashes)  # only digests are stored


async def test_approval_queues_exactly_one_execution(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    action = await _pending_action(session_factory)
    client = _client(session_factory)
    response = _approve(client, _login(client, "approver"), action, reason="lag evidence is clear")
    assert response.json() == {"outcome": "approved", "action_state": "QUEUED"}
    with session_factory() as session:
        approval = session.scalars(select(ApprovalRequestRow)).one()
        decision = session.scalars(select(ApprovalDecisionRow)).one()
    assert approval.state == "APPROVED"
    assert (decision.approver_id, decision.action_hash) == ("user:approver", action["hash"])
    assert _count(session_factory, OutboxJobRow, OutboxJobRow.kind == EXECUTE_ACTION_JOB) == 1
    detail = client.get(f"/api/v1/actions/{action['id']}").json()
    assert detail["state"] == "QUEUED"
    assert detail["decisions"][0]["decision"] == "APPROVED"


@pytest.mark.parametrize("username", ["viewer", "operator", "administrator"])
async def test_only_approvers_can_approve(
    session_factory: sessionmaker[Session], username: str
) -> None:
    # S-06: unauthorized roles cannot sign the action; the attempt is audited.
    _seed(session_factory)
    action = await _pending_action(session_factory)
    client = _client(session_factory)
    assert _approve(client, _login(client, username), action).status_code == 403
    assert _approve(client, {}, action).status_code == 401
    assert _count(session_factory, OutboxJobRow, OutboxJobRow.kind == EXECUTE_ACTION_JOB) == 0
    assert (
        _count(session_factory, AuditEventRow, AuditEventRow.event_type == "approve_refused") == 1
    )


async def test_tampered_hash_and_stale_version_are_rejected(
    session_factory: sessionmaker[Session],
) -> None:
    # S-04: the approver must sign the exact envelope that was proposed.
    _seed(session_factory)
    action = await _pending_action(session_factory)
    client = _client(session_factory)
    auth = _login(client, "approver")
    tampered = _approve(client, auth, action, action_hash="sha256:" + "0" * 64)
    assert tampered.status_code == 409
    assert tampered.json()["detail"]["outcome"] == "hash_mismatch"
    stale = _approve(client, auth, action, expected_version=action["version"] + 5)
    assert stale.json()["detail"]["outcome"] == "version_conflict"
    assert _count(session_factory, OutboxJobRow, OutboxJobRow.kind == EXECUTE_ACTION_JOB) == 0


async def test_expired_approval_is_refused_and_escalated(
    session_factory: sessionmaker[Session],
) -> None:
    # S-03: past the TTL the approval can no longer authorize anything.
    _seed(session_factory)
    action = await _pending_action(session_factory)
    with session_factory.begin() as session:
        session.execute(
            update(ActionProposalRow).values(expires_at=utcnow() - timedelta(seconds=1))
        )
    client = _client(session_factory)
    response = _approve(client, _login(client, "approver"), action)
    assert response.json()["detail"]["outcome"] == "expired"
    with session_factory() as session:
        assert session.get_one(ActionProposalRow, action["id"]).state == "EXPIRED"
        incident = session.get_one(IncidentRow, action["incident_id"])
    assert (incident.state, incident.escalation_reason) == ("ESCALATED", "approval_expired")
    assert _count(session_factory, OutboxJobRow, OutboxJobRow.kind == EXECUTE_ACTION_JOB) == 0


async def test_concurrent_double_click_dispatches_once(
    session_factory: sessionmaker[Session],
) -> None:
    # S-05: two approvers click at the same time; exactly one decision wins.
    _seed(session_factory)
    with session_factory.begin() as session:
        create_user(session, "approver2", PASSWORD, HumanRole.APPROVER)
    action = await _pending_action(session_factory)
    client = _client(session_factory)
    auths = [_login(client, "approver"), _login(client, "approver2")]
    barrier = threading.Barrier(2)
    codes: list[int] = []

    def click(auth: dict[str, str]) -> None:
        barrier.wait()
        codes.append(_approve(client, auth, action).status_code)

    threads = [threading.Thread(target=click, args=(a,)) for a in auths]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(codes) == [200, 409]
    assert _count(session_factory, ApprovalDecisionRow) == 1
    assert _count(session_factory, OutboxJobRow, OutboxJobRow.kind == EXECUTE_ACTION_JOB) == 1


async def test_reject_escalates_without_dispatch(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)
    action = await _pending_action(session_factory)
    client = _client(session_factory)
    response = client.post(
        f"/api/v1/actions/{action['id']}/reject",
        json={"expected_version": action["version"], "reason": "not convinced"},
        headers=_login(client, "approver"),
    )
    assert response.json() == {"outcome": "rejected", "action_state": "REJECTED"}
    with session_factory() as session:
        incident = session.get_one(IncidentRow, action["incident_id"])
    assert (incident.state, incident.escalation_reason) == ("ESCALATED", "approval_rejected")
    assert _count(session_factory, OutboxJobRow, OutboxJobRow.kind == EXECUTE_ACTION_JOB) == 0


async def test_operator_can_cancel_a_queued_action(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)
    action = await _pending_action(session_factory)
    client = _client(session_factory)
    _approve(client, _login(client, "approver"), action)
    queued_version = client.get(f"/api/v1/actions/{action['id']}").json()["version"]
    viewer = client.post(
        f"/api/v1/actions/{action['id']}/cancel",
        json={"expected_version": queued_version},
        headers=_login(client, "viewer"),
    )
    assert viewer.status_code == 403
    response = client.post(
        f"/api/v1/actions/{action['id']}/cancel",
        json={"expected_version": queued_version},
        headers=_login(client, "operator"),
    )
    assert response.json() == {"outcome": "cancelled", "action_state": "CANCELLED"}
    with session_factory() as session:
        assert session.scalars(select(ApprovalRequestRow)).one().state == "REVOKED"


async def test_separation_of_duties(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)
    action = await _pending_action(session_factory)
    with session_factory.begin() as session:
        session.execute(update(ApprovalRequestRow).values(requested_by="user:approver"))
    client = _client(session_factory)
    response = _approve(client, _login(client, "approver"), action)
    assert response.json()["detail"]["outcome"] == "separation_of_duties"


async def test_expiry_sweep(session_factory: sessionmaker[Session]) -> None:
    action = await _pending_action(session_factory)
    with session_factory.begin() as session:
        assert expire_due(session, utcnow(), "service:test") == 0
        session.execute(
            update(ActionProposalRow).values(expires_at=utcnow() - timedelta(seconds=1))
        )
    with session_factory.begin() as session:
        assert expire_due(session, utcnow(), "service:test") == 1
    with session_factory() as session:
        assert session.get_one(ActionProposalRow, action["id"]).state == "EXPIRED"
