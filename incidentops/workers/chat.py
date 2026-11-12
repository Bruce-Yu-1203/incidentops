"""Chat worker: claims ``answer_chat`` jobs and runs the chat agent (ADR-023).

It has the same read-only agent identity as the investigation worker (agent
database login, lab read token, model key) but its own queue, so a question
never waits behind a long investigation.

Run with ``python -m incidentops.workers.chat``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket
import sys
from typing import Final

import httpx
from sqlalchemy.orm import Session, sessionmaker

from incidentops import telemetry
from incidentops.agent.baseline import RulesBaselineAdapter
from incidentops.agent.model import ModelAdapter
from incidentops.chat.agent import ChatAgent
from incidentops.chat.answerer import ChatAnswerer, ChatBusyError
from incidentops.chat.service import ANSWER_CHAT_JOB
from incidentops.config import Settings, get_settings
from incidentops.domain.catalog import load_catalog
from incidentops.evidence.store import DatabaseEvidenceSink
from incidentops.persistence import outbox
from incidentops.persistence.db import make_engine, make_session_factory
from incidentops.persistence.models import OutboxJobRow
from incidentops.security.identities import Role
from incidentops.tools.backends import HttpTelemetryBackend
from incidentops.tools.gateway import ToolGateway
from incidentops.trajectory.recorder import DatabaseTrajectorySink
from incidentops.workers.credentials import assert_credential_scope
from incidentops.workers.investigation import build_model

LOGGER = logging.getLogger("incidentops.workers.chat")
MAX_ATTEMPTS: Final = 3
BUSY_RETRY_SECONDS: Final = 2


async def process_chat_one(
    factory: sessionmaker[Session], answerer: ChatAnswerer, owner: str, lease_seconds: int
) -> bool:
    """Claim and answer one chat question. Returns False when none was waiting."""
    with factory.begin() as session:
        job = outbox.claim(session, ANSWER_CHAT_JOB, owner, lease_seconds)
        if job is None:
            return False
        job_id, message_id = job.id, str(job.payload["message_id"])
    try:
        await answerer.answer(message_id)
    except ChatBusyError:
        # One turn per incident at a time: wait for the running one to finish.
        with factory.begin() as session:
            outbox.defer(session, job_id, owner, BUSY_RETRY_SECONDS)
        return True
    except Exception as exc:
        LOGGER.exception("chat job %s failed", job_id)
        with factory.begin() as session:
            outbox.release_for_retry(
                session,
                job_id,
                owner,
                type(exc).__name__,
                retry_delay_seconds=5,
                max_attempts=MAX_ATTEMPTS,
            )
            dead = session.get_one(OutboxJobRow, job_id).status == "DEAD"
        if dead:
            answerer.give_up(message_id, type(exc).__name__)
        return True
    with factory.begin() as session:
        outbox.complete(session, job_id, owner)
    return True


def chat_model(model: ModelAdapter) -> ModelAdapter | None:
    """The rules baseline cannot read questions; chat then answers from rules."""
    return None if isinstance(model, RulesBaselineAdapter) else model


async def serve(settings: Settings) -> None:  # pragma: no cover - process entry point
    assert_credential_scope(Role.AGENT, settings)
    model = chat_model(build_model(settings))
    factory = make_session_factory(make_engine(settings.control_database_url.get_secret_value()))
    owner = f"chat-{socket.gethostname()}-{os.getpid()}"
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    async with httpx.AsyncClient(timeout=10) as client:
        backend = HttpTelemetryBackend(
            client,
            settings.prometheus_url,
            settings.loki_url,
            settings.tempo_url,
            settings.lab_read_token.get_secret_value() if settings.lab_read_token else None,
            settings.endpoint_overrides,
        )
        catalog = load_catalog()
        trajectory = DatabaseTrajectorySink(factory)
        gateway = ToolGateway(catalog, backend, DatabaseEvidenceSink(factory), trajectory)
        agent = ChatAgent(model, gateway, trajectory) if model is not None else None
        answerer = ChatAnswerer(factory, agent)
        LOGGER.info(
            "chat worker %s started (model %s)", owner, model.model_id if model else "rules"
        )
        while not stop.is_set():
            if not await process_chat_one(factory, answerer, owner, settings.worker_lease_seconds):
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=1)


def main() -> None:  # pragma: no cover - process entry point
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    telemetry.configure("incidentops-chat-worker")
    asyncio.run(serve(get_settings()))


if __name__ == "__main__":  # pragma: no cover
    main()
