"""Remediation adapters (architecture §9.4). No shell, no arbitrary targets.

An adapter maps an allowlisted ``action_type`` to one hard-coded controller
call. Network destinations come from the catalog (plus server-side overrides),
never from the envelope.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

import httpx

from incidentops.domain.actions import ActionEnvelope
from incidentops.domain.catalog import ServiceCatalog


class ApplyStatus(StrEnum):
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    AMBIGUOUS = "AMBIGUOUS"


class LookupStatus(StrEnum):
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ApplyResult:
    status: ApplyStatus
    receipt: dict[str, Any] = field(default_factory=dict)
    detail: str | None = None


@dataclass(frozen=True)
class LookupResult:
    status: LookupStatus
    record: dict[str, Any] = field(default_factory=dict)


class RemediationAdapter(Protocol):
    provider: str

    async def apply(self, envelope: ActionEnvelope) -> ApplyResult: ...

    async def get_operation(self, envelope: ActionEnvelope) -> LookupResult: ...

    async def inspect_target(self, target_id: str) -> str | None: ...


class ConsumerResumeAdapter:
    """``resume_test_consumer`` via the lab worker's management API."""

    provider = "lab_consumer_controller"

    def __init__(
        self,
        client: httpx.AsyncClient,
        catalog: ServiceCatalog,
        executor_token: str,
        endpoint_overrides: dict[str, str] | None = None,
    ) -> None:
        self._client = client
        self._catalog = catalog
        self._headers = {"Authorization": f"Bearer {executor_token}"}
        self._overrides = dict(endpoint_overrides or {})

    def _base(self, target_id: str) -> str:
        service = self._catalog.get(target_id)
        if service.management_url is None:
            raise ValueError(f"{target_id} has no management endpoint")
        return self._overrides.get(target_id, str(service.management_url)).rstrip("/")

    async def apply(self, envelope: ActionEnvelope) -> ApplyResult:
        body = {
            "action_id": envelope.action_id,
            "desired_paused": envelope.arguments["desired_paused"],
            "expected_generation": envelope.target_revision,
        }
        try:
            response = await self._client.post(
                f"{self._base(envelope.target_id)}/control/resume", json=body, headers=self._headers
            )
        except httpx.HTTPError as exc:
            # The request may or may not have reached the controller.
            return ApplyResult(ApplyStatus.AMBIGUOUS, detail=type(exc).__name__)
        if response.status_code in (200, 201):
            return ApplyResult(ApplyStatus.COMPLETED, dict(response.json()))
        if 400 <= response.status_code < 500:
            # A definitive refusal: the controller did not change state.
            detail = response.json().get("error") if _is_json(response) else None
            return ApplyResult(ApplyStatus.REJECTED, detail=str(detail or response.status_code))
        return ApplyResult(ApplyStatus.AMBIGUOUS, detail=f"http_{response.status_code}")

    async def get_operation(self, envelope: ActionEnvelope) -> LookupResult:
        try:
            response = await self._client.get(
                f"{self._base(envelope.target_id)}/control/operations/{envelope.action_id}",
                headers=self._headers,
            )
        except httpx.HTTPError:
            return LookupResult(LookupStatus.UNKNOWN)
        if response.status_code == 200:
            return LookupResult(LookupStatus.FOUND, dict(response.json()))
        if response.status_code == 404:
            return LookupResult(LookupStatus.NOT_FOUND)
        return LookupResult(LookupStatus.UNKNOWN)

    async def inspect_target(self, target_id: str) -> str | None:
        try:
            response = await self._client.get(
                f"{self._base(target_id)}/control/status", headers=self._headers
            )
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        generation = response.json().get("generation")
        return generation if isinstance(generation, str) else None


def _is_json(response: httpx.Response) -> bool:
    return bool(response.headers.get("content-type", "").startswith("application/json"))


class LabControllerAdapter:
    """The P5 remediations, each a single allowlisted call to the lab controller.

    ``POST /remediations`` carries the stable action_id, the action type, the
    registered target, the approved target revision and the policy-checked
    arguments. The controller refuses anything outside its own allowlist too.
    """

    provider = "lab_fault_controller"

    def __init__(
        self,
        client: httpx.AsyncClient,
        catalog: ServiceCatalog,
        executor_token: str,
        endpoint_overrides: dict[str, str] | None = None,
    ) -> None:
        self._client = client
        self._catalog = catalog
        self._headers = {"Authorization": f"Bearer {executor_token}"}
        self._overrides = dict(endpoint_overrides or {})

    def _base(self, target_id: str) -> str:
        service = self._catalog.get(target_id)
        if service.management_url is None:
            raise ValueError(f"{target_id} has no management endpoint")
        return self._overrides.get(target_id, str(service.management_url)).rstrip("/")

    async def apply(self, envelope: ActionEnvelope) -> ApplyResult:
        body = {
            "action_id": envelope.action_id,
            "action_type": envelope.action_type,
            "target_id": envelope.target_id,
            "expected_revision": envelope.target_revision,
            "arguments": dict(envelope.arguments),
        }
        try:
            response = await self._client.post(
                f"{self._base(envelope.target_id)}/remediations", json=body, headers=self._headers
            )
        except httpx.HTTPError as exc:
            return ApplyResult(ApplyStatus.AMBIGUOUS, detail=type(exc).__name__)
        if response.status_code in (200, 201):
            return ApplyResult(ApplyStatus.COMPLETED, dict(response.json()))
        if 400 <= response.status_code < 500:
            detail = response.json().get("error") if _is_json(response) else None
            return ApplyResult(ApplyStatus.REJECTED, detail=str(detail or response.status_code))
        return ApplyResult(ApplyStatus.AMBIGUOUS, detail=f"http_{response.status_code}")

    async def get_operation(self, envelope: ActionEnvelope) -> LookupResult:
        try:
            response = await self._client.get(
                f"{self._base(envelope.target_id)}/remediations/operations/{envelope.action_id}",
                headers=self._headers,
            )
        except httpx.HTTPError:
            return LookupResult(LookupStatus.UNKNOWN)
        if response.status_code == 200:
            return LookupResult(LookupStatus.FOUND, dict(response.json()))
        if response.status_code == 404:
            return LookupResult(LookupStatus.NOT_FOUND)
        return LookupResult(LookupStatus.UNKNOWN)

    async def inspect_target(self, target_id: str) -> str | None:
        try:
            response = await self._client.get(
                f"{self._base(target_id)}/targets/{target_id}", headers=self._headers
            )
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        revision = response.json().get("revision")
        return revision if isinstance(revision, str) else None


LAB_CONTROLLER_ACTIONS = (
    "release_lab_db_blocker",
    "release_lab_idle_connections",
    "reset_lab_load_profile",
    "restart_lab_dependency",
    "revert_lab_config",
    "rollback_demo_api",
    "stop_lab_cpu_load",
)
