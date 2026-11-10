"""LabControllerAdapter request shapes and outcome mapping."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from incidentops.domain.actions import ActionEnvelope
from incidentops.domain.catalog import load_catalog
from incidentops.execution.adapters import (
    ApplyStatus,
    LabControllerAdapter,
    LookupStatus,
)

ACTION_ID = "act_" + "a" * 32


def _envelope(**overrides: object) -> ActionEnvelope:
    fields: dict[str, object] = {
        "action_id": ACTION_ID,
        "action_type": "rollback_demo_api",
        "environment": "fault-lab",
        "target_id": "lab-api",
        "target_revision": "rel-2",
        "arguments": {"target_release": "rel-1"},
        "requested_by": "run:x",
        "evidence_ids": (),
        "policy_version": "p2",
        "expires_at": datetime.now(UTC) + timedelta(minutes=5),
    }
    fields.update(overrides)
    return ActionEnvelope(**fields)


def _adapter(handler: httpx.MockTransport) -> tuple[LabControllerAdapter, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=handler)
    overrides = {"lab-api": "http://controller.test"}
    return LabControllerAdapter(client, load_catalog(), "exec-token-0123456789", overrides), client


async def test_apply_sends_exact_envelope_fields() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"status": "COMPLETED", "revision_after": "rel-1"})

    adapter, client = _adapter(httpx.MockTransport(handler))
    async with client:
        result = await adapter.apply(_envelope())
    assert result.status is ApplyStatus.COMPLETED
    request = seen[0]
    assert str(request.url) == "http://controller.test/remediations"
    assert request.headers["Authorization"] == "Bearer exec-token-0123456789"
    assert json.loads(request.content) == {
        "action_id": ACTION_ID,
        "action_type": "rollback_demo_api",
        "target_id": "lab-api",
        "expected_revision": "rel-2",
        "arguments": {"target_release": "rel-1"},
    }


@pytest.mark.parametrize(
    ("status", "expected"),
    [(200, ApplyStatus.COMPLETED), (409, ApplyStatus.REJECTED), (503, ApplyStatus.AMBIGUOUS)],
)
async def test_apply_maps_status_codes(status: int, expected: ApplyStatus) -> None:
    adapter, client = _adapter(
        httpx.MockTransport(lambda r: httpx.Response(status, json={"error": "stale_revision"}))
    )
    async with client:
        assert (await adapter.apply(_envelope())).status is expected


async def test_network_failure_is_ambiguous_not_failed() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    adapter, client = _adapter(httpx.MockTransport(boom))
    async with client:
        assert (await adapter.apply(_envelope())).status is ApplyStatus.AMBIGUOUS


async def test_lookup_and_inspect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/remediations/operations/"):
            return httpx.Response(404, json={})
        return httpx.Response(200, json={"target_id": "lab-api", "revision": "rel-2"})

    adapter, client = _adapter(httpx.MockTransport(handler))
    async with client:
        assert (await adapter.get_operation(_envelope())).status is LookupStatus.NOT_FOUND
        assert await adapter.inspect_target("lab-api") == "rel-2"
