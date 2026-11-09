"""Phase 0 gate against the running Fault Lab (``make lab-up`` first).

Skipped when the lab is not reachable unless INCIDENTOPS_REQUIRE_LAB=1.
"""

from __future__ import annotations

import os

import httpx
import pytest
from fault_lab.scripts.labctl import LabController, LabEndpoints, run_smoke

pytestmark = pytest.mark.lab


@pytest.fixture
def lab() -> tuple[LabController, httpx.Client, dict[str, str]]:
    token = os.environ.get("FAULT_LAB_BENCHMARK_TOKEN", "")
    if not token:
        if os.environ.get("INCIDENTOPS_REQUIRE_LAB") == "1":
            pytest.fail("FAULT_LAB_BENCHMARK_TOKEN is required")
        pytest.skip("FAULT_LAB_BENCHMARK_TOKEN not set")
    endpoints = LabEndpoints()
    client = httpx.Client(timeout=5)
    try:
        client.get(f"{endpoints.consumer}/healthz").raise_for_status()
    except httpx.HTTPError:
        client.close()
        if os.environ.get("INCIDENTOPS_REQUIRE_LAB") == "1":
            pytest.fail("Fault Lab is required but unreachable")
        pytest.skip("Fault Lab not running; run `make lab-up`")
    others = {
        "executor": os.environ.get("FAULT_LAB_EXECUTOR_TOKEN", ""),
        "reader": os.environ.get("FAULT_LAB_READ_TOKEN", ""),
    }
    return LabController(endpoints, token, client), client, others


def test_consumer_pause_reset_gate(lab: tuple[LabController, httpx.Client, dict[str, str]]) -> None:
    controller, client, others = lab
    try:
        run_smoke(controller, client, {k: v for k, v in others.items() if v})
    finally:
        client.close()
