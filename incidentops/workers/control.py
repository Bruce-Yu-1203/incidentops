"""Control worker: proposal evaluation and approval expiry (trusted API identity).

Run with ``python -m incidentops.workers.control``.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

import httpx

from incidentops.actions.approvals import expire_due
from incidentops.actions.proposals import ProposalEvaluator
from incidentops.actions.targets import ConsumerTargetInspector
from incidentops.agent.run_manager import EVALUATE_PROPOSAL_JOB
from incidentops.config import Settings, get_settings
from incidentops.domain.catalog import load_catalog
from incidentops.persistence.db import make_engine, make_session_factory
from incidentops.policy.engine import PolicyEngine, load_policy
from incidentops.security.identities import Role
from incidentops.time import utcnow
from incidentops.tools.backends import HttpTelemetryBackend
from incidentops.workers.common import Handler, owner_id, run_forever
from incidentops.workers.credentials import assert_credential_scope


def handlers(evaluator: ProposalEvaluator) -> dict[str, Handler]:
    async def evaluate(payload: dict[str, Any], _: int) -> None:
        await evaluator.evaluate(str(payload["incident_id"]), str(payload["diagnosis_id"]))

    return {EVALUATE_PROPOSAL_JOB: evaluate}


async def serve(settings: Settings) -> None:  # pragma: no cover - process entry point
    assert_credential_scope(Role.API, settings)
    factory = make_session_factory(make_engine(settings.control_database_url.get_secret_value()))
    catalog = load_catalog()
    async with httpx.AsyncClient(timeout=5) as client:
        backend = HttpTelemetryBackend(
            client,
            settings.prometheus_url,
            settings.loki_url,
            settings.tempo_url,
            settings.lab_read_token.get_secret_value() if settings.lab_read_token else None,
            settings.endpoint_overrides,
        )
        evaluator = ProposalEvaluator(
            factory, PolicyEngine(load_policy(), catalog), ConsumerTargetInspector(catalog, backend)
        )

        async def sweep() -> None:
            with factory.begin() as session:
                expire_due(session, utcnow(), "service:approval-sweeper")

        await run_forever(factory, handlers(evaluator), owner_id("control"), 120, idle=sweep)


def main() -> None:  # pragma: no cover
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(serve(get_settings()))


if __name__ == "__main__":  # pragma: no cover
    main()
