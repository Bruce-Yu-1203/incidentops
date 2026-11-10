"""Verifier worker: independent read-only recovery checks.

Run with ``python -m incidentops.workers.verifier``.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

import httpx

from incidentops.actions.targets import ConsumerTargetInspector
from incidentops.config import Settings, get_settings
from incidentops.domain.catalog import load_catalog
from incidentops.execution.executor import VERIFY_ACTION_JOB
from incidentops.persistence.db import make_engine, make_session_factory
from incidentops.security.identities import Role
from incidentops.tools.backends import HttpTelemetryBackend
from incidentops.verification.verifier import RecoveryVerifier, load_verify_config
from incidentops.workers.common import Handler, owner_id, run_forever
from incidentops.workers.credentials import assert_credential_scope


def handlers(verifier: RecoveryVerifier) -> dict[str, Handler]:
    async def verify(payload: dict[str, Any], _: int) -> None:
        await verifier.verify(str(payload["action_id"]))

    return {VERIFY_ACTION_JOB: verify}


async def serve(settings: Settings) -> None:  # pragma: no cover - process entry point
    assert_credential_scope(Role.VERIFIER, settings)
    factory = make_session_factory(make_engine(settings.control_database_url.get_secret_value()))
    catalog = load_catalog()
    config = load_verify_config()
    async with httpx.AsyncClient(timeout=5) as client:
        backend = HttpTelemetryBackend(
            client,
            settings.prometheus_url,
            settings.loki_url,
            settings.tempo_url,
            settings.lab_read_token.get_secret_value() if settings.lab_read_token else None,
            settings.endpoint_overrides,
        )
        verifier = RecoveryVerifier(
            factory, catalog, backend, ConsumerTargetInspector(catalog, backend), config
        )
        # Heartbeats keep the lease while a long observation window runs.
        await run_forever(factory, handlers(verifier), owner_id("verifier"), 60)


def main() -> None:  # pragma: no cover
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(serve(get_settings()))


if __name__ == "__main__":  # pragma: no cover
    main()
