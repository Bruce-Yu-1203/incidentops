"""The executor's adapter registry covers every action policy can allow."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from incidentops.domain.actions import ActionEnvelope
from incidentops.domain.catalog import load_catalog
from incidentops.execution.adapters import ApplyStatus, LabControllerAdapter
from incidentops.policy.engine import load_policy
from incidentops.workers.executor import build_adapters

ACTION_ID = "act_" + "b" * 32


def _registry() -> dict[str, object]:
    client = httpx.AsyncClient()
    return dict(build_adapters(client, load_catalog(), "exec-token-0123456789", {}))


def test_every_policy_allowed_action_has_an_executor_adapter() -> None:
    policy = load_policy()
    allowed = {
        action
        for cause in policy.root_cause_actions
        for action in policy.actions_for_root_cause(cause)
    }
    assert allowed, "policy allows no actions"
    assert sorted(allowed - set(_registry())) == []


def test_every_catalog_action_has_an_executor_adapter() -> None:
    allowed = {a for s in load_catalog().services for a in s.allowed_actions}
    assert sorted(allowed - set(_registry())) == []


@pytest.mark.parametrize(
    ("action_type", "target_id", "arguments"),
    [
        ("reset_lab_load_profile", "lab-loadgen", {}),
        ("revert_lab_config", "lab-api-config", {"target_config": "cfg-1"}),
        ("release_lab_idle_connections", "lab-postgres", {}),
    ],
)
async def test_new_lab_actions_post_to_the_controller(
    action_type: str, target_id: str, arguments: dict[str, str]
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"status": "COMPLETED", "revision_after": "r2"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = build_adapters(
        client, load_catalog(), "exec-token-0123456789", {target_id: "http://controller.test"}
    )[action_type]
    assert isinstance(adapter, LabControllerAdapter)
    envelope = ActionEnvelope(
        action_id=ACTION_ID,
        action_type=action_type,
        environment="fault-lab",
        target_id=target_id,
        target_revision="r1",
        arguments=arguments,
        requested_by="run:x",
        evidence_ids=(),
        policy_version="p5",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    async with client:
        result = await adapter.apply(envelope)
    assert result.status is ApplyStatus.COMPLETED
    assert str(seen[0].url) == "http://controller.test/remediations"
    assert json.loads(seen[0].content) == {
        "action_id": ACTION_ID,
        "action_type": action_type,
        "target_id": target_id,
        "expected_revision": "r1",
        "arguments": arguments,
    }
