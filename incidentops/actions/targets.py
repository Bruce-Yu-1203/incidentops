"""Read-only inspection of remediation targets (current revision and state)."""

from __future__ import annotations

from typing import Any, Protocol

from incidentops.domain.catalog import ServiceCatalog, ServiceKind, UnknownServiceError
from incidentops.tools.backends import TelemetryBackend


class TargetInspector(Protocol):
    async def inspect(self, target_id: str) -> dict[str, Any] | None:
        """Return the target's current state including ``revision``, or None."""
        ...


class ConsumerTargetInspector:
    """Reads ``GET /control/status`` with the read-only lab token."""

    def __init__(self, catalog: ServiceCatalog, backend: TelemetryBackend) -> None:
        self._catalog = catalog
        self._backend = backend

    async def inspect(self, target_id: str) -> dict[str, Any] | None:
        try:
            service = self._catalog.get(target_id)
        except UnknownServiceError:
            return None
        if service.kind is not ServiceKind.KAFKA_CONSUMER or service.management_url is None:
            return None
        try:
            status = await self._backend.consumer_status(target_id, str(service.management_url))
        except Exception:
            return None
        generation = status.get("generation")
        if not isinstance(generation, str):
            return None
        return {"revision": generation, "paused": status.get("paused")}
