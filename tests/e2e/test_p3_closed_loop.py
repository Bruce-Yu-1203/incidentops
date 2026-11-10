"""Roadmap P3 gate: B-F03 closed loop against the live lab, with a real approval.

Prerequisites: lab-up, infra-up, seed-users, and api-run, control-run,
executor-run, verifier-run plus an investigation worker (the rules baseline
``INCIDENTOPS_MODEL_ID=rules-baseline-v1`` works without a provider key).

1. Approved path: pause -> real alert -> diagnosis -> policy -> human approval
   of the exact hash -> executor resume -> independent verification -> RESOLVED.
2. Rejected path: the same proposal is rejected and the lab sees zero
   external mutations from IncidentOps.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fault_lab.scripts.labctl import LabController, LabEndpoints

from tests.e2e.session import API
from tests.e2e.test_p1_alert_gate import _wait_for_quiet_baseline

pytestmark = pytest.mark.lab
TIMEOUT_S = 420


@pytest.fixture
def env() -> Iterator[tuple[LabController, httpx.Client, dict[str, str]]]:
    token = os.environ.get("FAULT_LAB_BENCHMARK_TOKEN", "")
    password = os.environ.get("INCIDENTOPS_DEMO_APPROVER_PASSWORD", "")
    client = httpx.Client(timeout=10)
    try:
        client.get(f"{API}/healthz").raise_for_status()
    except httpx.HTTPError:
        client.close()
        if os.environ.get("INCIDENTOPS_REQUIRE_LAB") == "1":
            pytest.fail("API and workers are required")
        pytest.skip("start the API and workers first")
    if not (token and password):
        client.close()
        pytest.skip("benchmark token or demo approver password missing")
    login = client.post(
        f"{API}/api/v1/auth/login", json={"username": "approver", "password": password}
    )
    login.raise_for_status()
    auth = {"Authorization": f"Session {login.headers['X-Session-Token']}"}
    client.cookies.clear()
    lab = LabController(LabEndpoints(), token, client)
    try:
        yield lab, client, auth
    finally:
        lab.reset()
        client.close()


def _poll(predicate: Callable[[], Any], what: str, timeout_s: float = TIMEOUT_S) -> Any:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(5)
    raise AssertionError(f"timed out waiting for {what}")


def _new_incident(client: httpx.Client, auth: dict[str, str], since: datetime) -> dict[str, Any]:
    for incident in client.get(f"{API}/api/v1/incidents", headers=auth).json():
        created = datetime.fromisoformat(incident["created_at"])
        if incident["service_id"] == "persist-demo-consumer" and created >= since:
            return dict(client.get(f"{API}/api/v1/incidents/{incident['id']}", headers=auth).json())
    return {}


def _pending_action(client: httpx.Client, auth: dict[str, str], since: datetime) -> dict[str, Any]:
    def find() -> dict[str, Any] | None:
        incident = _new_incident(client, auth, since)
        pending = [a for a in incident.get("actions", []) if a["state"] == "PENDING_APPROVAL"]
        if pending:
            return dict(client.get(f"{API}/api/v1/actions/{pending[0]['id']}", headers=auth).json())
        return None

    return dict(_poll(find, "a pending approval"))


def _start_trial(lab: LabController, client: httpx.Client) -> datetime:
    lab.reset()
    _wait_for_quiet_baseline(client)
    started = datetime.now(UTC)
    lab.pause()
    return started


def _operation(lab: LabController, client: httpx.Client, action_id: str) -> httpx.Response:
    return client.get(
        f"{lab.endpoints.consumer}/control/operations/{action_id}",
        headers={"Authorization": f"Bearer {os.environ['FAULT_LAB_BENCHMARK_TOKEN']}"},
    )


def test_approved_resume_is_executed_once_and_verified(
    env: tuple[LabController, httpx.Client, dict[str, str]],
) -> None:
    lab, client, auth = env
    started = _start_trial(lab, client)
    action = _pending_action(client, auth, started)
    assert action["envelope"]["arguments"] == {"desired_paused": False}
    assert action["envelope"]["target_revision"] == lab.status()["generation"]

    decision = client.post(
        f"{API}/api/v1/actions/{action['id']}/approve",
        json={"action_hash": action["action_hash"], "expected_version": action["version"]},
        headers=auth,
    )
    assert decision.status_code == 200, decision.text

    def resolved() -> dict[str, Any] | None:
        incident = client.get(
            f"{API}/api/v1/incidents/{action['incident_id']}", headers=auth
        ).json()
        return incident if incident["state"] in {"RESOLVED", "ESCALATED"} else None

    incident = _poll(resolved, "verification")
    detail = client.get(f"{API}/api/v1/actions/{action['id']}", headers=auth).json()
    assert incident["state"] == "RESOLVED", (incident["escalation_reason"], detail["verifications"])
    assert detail["state"] == "SUCCEEDED"
    assert detail["verifications"][-1]["outcome"] == "PASSED"
    assert lab.status()["paused"] is False
    operation = _operation(lab, client, action["id"]).json()
    assert (operation["operation"], operation["requested_by"]) == ("resume", "executor")


def test_rejected_resume_causes_zero_external_mutations(
    env: tuple[LabController, httpx.Client, dict[str, str]],
) -> None:
    lab, client, auth = env
    started = _start_trial(lab, client)
    action = _pending_action(client, auth, started)
    before = lab.status()
    decision = client.post(
        f"{API}/api/v1/actions/{action['id']}/reject",
        json={"expected_version": action["version"], "reason": "P3 gate negative path"},
        headers=auth,
    )
    assert decision.status_code == 200, decision.text
    time.sleep(30)  # give the executor time to (wrongly) act if it ever would
    after = lab.status()
    assert after == before  # still paused, same generation
    assert _operation(lab, client, action["id"]).status_code == 404
    incident = client.get(f"{API}/api/v1/incidents/{action['incident_id']}", headers=auth).json()
    assert (incident["state"], incident["escalation_reason"]) == ("ESCALATED", "approval_rejected")
