"""Read-only inspection of remediation targets (current revision and state)."""

from __future__ import annotations

import logging
from typing import Any, Protocol

from incidentops.domain.catalog import ServiceCatalog, ServiceKind, UnknownServiceError
from incidentops.tools.backends import TelemetryBackend

LOGGER = logging.getLogger(__name__)


class TargetInspector(Protocol):
    async def inspect(self, target_id: str) -> dict[str, Any] | None:
        """Return the target's current state including ``revision``, or None."""
        ...


class ConsumerTargetInspector:
    """Reads a target's current revision with the read-only lab token.

    Consumers report their generation via ``GET /control/status``; targets of
    the lab controller report theirs via ``GET /targets/{id}``.
    """

    def __init__(self, catalog: ServiceCatalog, backend: TelemetryBackend) -> None:
        self._catalog = catalog
        self._backend = backend

    async def inspect(self, target_id: str) -> dict[str, Any] | None:
        try:
            service = self._catalog.get(target_id)
        except UnknownServiceError:
            return None
        if service.management_url is None:
            return None
        url = str(service.management_url)
        try:
            if service.kind is ServiceKind.KAFKA_CONSUMER:
                status = await self._backend.consumer_status(target_id, url)
                generation = status.get("generation")
                if not isinstance(generation, str):
                    return None
                return {"revision": generation, "paused": status.get("paused")}
            state = await self._backend.controller_get(target_id, url, f"/targets/{target_id}")
        except Exception as exc:
            LOGGER.warning("target revision read failed for %s: %s", target_id, type(exc).__name__)
            return None
        revision = state.get("revision")
        return {"revision": revision} if isinstance(revision, str) else None
