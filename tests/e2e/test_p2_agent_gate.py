"""Roadmap P2 gate with a real model: a lab fault ends in an evidence-linked diagnosis.

Prerequisites: lab-up, infra-up, api-run and worker-run (which needs
INCIDENTOPS_MODEL_ID and INCIDENTOPS_ANTHROPIC_API_KEY in .env). The check
asserts validity and evidence linkage only; diagnostic accuracy is measured by
the P5 benchmark, never asserted from a single run.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fault_lab.scripts.labctl import LabController, LabEndpoints

from tests.e2e.session import API, sign_in_viewer
from tests.e2e.test_p1_alert_gate import _wait_for_quiet_baseline

pytestmark = pytest.mark.lab
DIAGNOSIS_TIMEOUT_S = 600


@pytest.fixture
def env() -> Iterator[tuple[LabController, httpx.Client]]:
    token = os.environ.get("FAULT_LAB_BENCHMARK_TOKEN", "")
    if not os.environ.get("P2_GATE_MODEL_CONFIGURED"):
        pytest.skip("set P2_GATE_MODEL_CONFIGURED=1 once worker-run uses a real model")
    client = httpx.Client(timeout=10)
    try:
        client.get(f"{API}/healthz").raise_for_status()
    except httpx.HTTPError:
        client.close()
        pytest.fail("API is required: run `make api-run` and `make worker-run`")
    sign_in_viewer(client)
    try:
        yield LabController(LabEndpoints(), token, client), client
    finally:
        client.close()


def _wait_for_diagnosis(client: httpx.Client, service_id: str, since: datetime) -> dict[str, Any]:
    deadline = time.monotonic() + DIAGNOSIS_TIMEOUT_S
    while time.monotonic() < deadline:
        for incident in client.get(f"{API}/api/v1/incidents").json():
            created = datetime.fromisoformat(incident["created_at"])
            if incident["service_id"] != service_id or created < since:
                continue
            if incident["state"] not in {"RECEIVED", "TRIAGED", "INVESTIGATING"}:
                return dict(client.get(f"{API}/api/v1/incidents/{incident['id']}").json())
        time.sleep(10)
    raise AssertionError("no finished investigation within the timeout")


def test_kafka_pause_gets_an_evidence_linked_diagnosis(
    env: tuple[LabController, httpx.Client],
) -> None:
    lab, client = env
    lab.reset()
    _wait_for_quiet_baseline(client)
    # Close any still-active incident for the service so this trial opens a new one.
    started = datetime.now(UTC)
    try:
        lab.pause()
        incident = _wait_for_diagnosis(client, "persist-demo-consumer", started)
    finally:
        lab.reset()
    assert incident["diagnosis"] is not None, incident["escalation_reason"]
    (run,) = [r for r in incident["runs"] if r["state"] != "FAILED"][-1:]
    detail = client.get(f"{API}/api/v1/investigations/{run['id']}").json()
    evidence_ids = {e["id"] for e in detail["evidence"]}
    cited = set(incident["diagnosis"]["diagnosis"]["supporting_evidence_ids"])
    assert cited
    assert cited <= evidence_ids
    print(
        "root cause:",
        incident["diagnosis"]["diagnosis"]["root_cause_id"],
        "| state:",
        incident["state"],
        "| tool calls:",
        run["tool_call_count"],
    )
