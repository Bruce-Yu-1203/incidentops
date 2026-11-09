"""Investigation worker: claims ``investigate_incident`` jobs and runs the agent.

Identity: read-only telemetry access plus its own evidence/trajectory/run
records. It holds no executor, fault-controller or Docker credentials.

Run with ``python -m incidentops.workers.investigation``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket
import sys

import httpx
from sqlalchemy.orm import Session, sessionmaker

from incidentops import telemetry
from incidentops.agent.graph import Budgets
from incidentops.agent.model import AnthropicModelAdapter
from incidentops.agent.run_manager import InvestigationRunner
from incidentops.config import Settings, get_settings
from incidentops.domain.catalog import load_catalog
from incidentops.evidence.store import DatabaseEvidenceSink
from incidentops.ingestion.service import INVESTIGATE_JOB
from incidentops.persistence import outbox
from incidentops.persistence.db import make_engine, make_session_factory
from incidentops.tools.backends import HttpTelemetryBackend
from incidentops.tools.gateway import ToolGateway
from incidentops.trajectory.recorder import DatabaseTrajectorySink

LOGGER = logging.getLogger("incidentops.workers.investigation")
MAX_ATTEMPTS = 3


async def process_one(
    factory: sessionmaker[Session],
    runner: InvestigationRunner,
    owner: str,
    lease_seconds: int,
) -> bool:
    """Claim and handle one job. Returns False when no job was available."""
    with factory.begin() as session:
        job = outbox.claim(session, INVESTIGATE_JOB, owner, lease_seconds)
        if job is None:
            return False
        job_id, incident_id = job.id, str(job.payload["incident_id"])
    try:
        outcome = await runner.run(incident_id)
    except Exception as exc:
        LOGGER.exception("investigation job %s failed", job_id)
        with factory.begin() as session:
            outbox.release_for_retry(
                session,
                job_id,
                owner,
                type(exc).__name__,
                retry_delay_seconds=30,
                max_attempts=MAX_ATTEMPTS,
            )
        return True
    with factory.begin() as session:
        if not outbox.complete(session, job_id, owner):
            LOGGER.warning("lease for job %s was lost before completion", job_id)
    LOGGER.info(
        "incident %s investigated: run=%s run_state=%s incident_state=%s",
        incident_id,
        outcome.run_id,
        outcome.run_state,
        outcome.incident_state,
    )
    return True


def budgets_from(settings: Settings) -> Budgets:
    return Budgets(
        max_steps=settings.max_agent_steps,
        max_tool_calls=settings.max_tool_calls,
        wall_clock_seconds=settings.investigation_timeout_seconds,
    )


async def serve(settings: Settings) -> None:  # pragma: no cover - process entry point
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    if not settings.model_id or settings.anthropic_api_key is None:
        raise SystemExit("INCIDENTOPS_MODEL_ID and INCIDENTOPS_ANTHROPIC_API_KEY are required")
    database_url = settings.control_database_url.get_secret_value()
    factory = make_session_factory(make_engine(database_url))
    owner = f"investigation-{socket.gethostname()}-{os.getpid()}"
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    async with (
        httpx.AsyncClient(timeout=10) as client,
        AsyncPostgresSaver.from_conn_string(database_url.replace("+psycopg", "")) as saver,
    ):
        await saver.setup()
        backend = HttpTelemetryBackend(
            client,
            settings.prometheus_url,
            settings.loki_url,
            settings.tempo_url,
            settings.lab_read_token.get_secret_value() if settings.lab_read_token else None,
            settings.endpoint_overrides,
        )
        evidence = DatabaseEvidenceSink(factory)
        trajectory = DatabaseTrajectorySink(factory)
        catalog = load_catalog()
        runner = InvestigationRunner(
            factory,
            catalog,
            AnthropicModelAdapter(settings.anthropic_api_key.get_secret_value(), settings.model_id),
            ToolGateway(catalog, backend, evidence, trajectory),
            evidence,
            trajectory,
            budgets_from(settings),
            checkpointer=saver,
        )
        LOGGER.info("investigation worker %s started (model %s)", owner, settings.model_id)
        while not stop.is_set():
            handled = await process_one(factory, runner, owner, settings.worker_lease_seconds)
            if not handled:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=2)


def main() -> None:  # pragma: no cover - process entry point
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    telemetry.configure("incidentops-investigation-worker")
    asyncio.run(serve(get_settings()))


if __name__ == "__main__":  # pragma: no cover
    main()
