"""Reliability / crash-injection suite R-01 .. R-08 (benchmark.md §9).

A crash is simulated with ``ProcessKilled``, a ``BaseException`` that, like a
SIGKILL, is not handled by any application ``except Exception`` block: the
transaction in progress is lost and leases stay held until they expire.
Invariants: committed work survives, nothing is fabricated, and no external
mutation is ever replayed blindly.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from incidentops.actions.approvals import EXECUTE_ACTION_JOB, approve
from incidentops.agent.model import ModelTurn, ScriptedModelAdapter
from incidentops.api.app import create_app
from incidentops.auth.service import AuthenticatedUser, HumanRole, create_user
from incidentops.config import Settings
from incidentops.domain.catalog import load_catalog
from incidentops.domain.schemas import VerificationOutcome
from incidentops.execution.adapters import ApplyResult, ApplyStatus, LookupResult, LookupStatus
from incidentops.execution.executor import ExecutionOutcome
from incidentops.persistence.models import (
    ActionProposalRow,
    AlertEventRow,
    ApprovalDecisionRow,
    EvidenceRow,
    IncidentRow,
    InvestigationRunRow,
    OutboxJobRow,
    VerificationRow,
)
from incidentops.time import utcnow
from incidentops.verification.verifier import RecoveryVerifier, VerifyConfig
from incidentops.workers import verifier as verifier_worker
from incidentops.workers.common import process_next
from incidentops.workers.investigation import process_one
from tests.integration.helpers import FakeAdapter, FakeInspector
from tests.integration.test_executor import _executor, _queued_action
from tests.integration.test_investigation_worker import OWNER, _ingest, _runner
from tests.integration.test_proposals import _evaluator
from tests.integration.test_verifier import SPEC, FakeClock, LagBackend, _verifying_action
from tests.unit.test_alertmanager_normalize import payload
from tests.unit.test_investigation_graph import lag_and_status, submit

pytestmark = [pytest.mark.integration, pytest.mark.recovery]

APPROVER = AuthenticatedUser("usr_a", "approver", HumanRole.APPROVER)


class ProcessKilledError(BaseException):
    """Simulated SIGKILL: escapes every ``except Exception`` handler."""


def _count(factory: sessionmaker[Session], model: Any, *where: Any) -> int:
    with factory() as session:
        return session.scalar(select(func.count()).select_from(model).where(*where)) or 0


def _expire_leases(factory: sessionmaker[Session]) -> None:
    with factory.begin() as session:
        session.execute(text("UPDATE outbox_jobs SET lease_until = now() - interval '1 second'"))


def test_r01_duplicate_alertmanager_events(session_factory: sessionmaker[Session]) -> None:
    client = TestClient(
        create_app(
            Settings(alertmanager_token="alertmanager-token-0123456789"),
            session_factory=session_factory,
        )
    )
    headers = {"Authorization": "Bearer alertmanager-token-0123456789"}
    for _ in range(5):
        assert (
            client.post("/api/v1/alerts/alertmanager", json=payload(), headers=headers).status_code
            == 202
        )
    assert _count(session_factory, IncidentRow) == 1
    assert _count(session_factory, AlertEventRow) == 5
    assert _count(session_factory, OutboxJobRow) == 1


async def test_r02_investigation_worker_killed_mid_run(
    session_factory: sessionmaker[Session],
) -> None:
    incident_id = _ingest(session_factory)

    def killed(_: list[dict[str, Any]]) -> ModelTurn:
        raise ProcessKilledError

    crashed = _runner(session_factory, ScriptedModelAdapter([lag_and_status(), killed]))
    with pytest.raises(ProcessKilledError):
        await process_one(session_factory, crashed, "dead-worker", 60)
    # Committed evidence from before the crash survives; the run is still open.
    evidence_before = _count(session_factory, EvidenceRow)
    assert evidence_before == 2
    assert _count(session_factory, InvestigationRunRow, InvestigationRunRow.state == "RUNNING") == 1

    _expire_leases(session_factory)
    healthy = _runner(session_factory, ScriptedModelAdapter([lag_and_status(), submit]))
    assert await process_one(session_factory, healthy, OWNER, 60)
    with session_factory() as session:
        runs = {r.state: r.error_code for r in session.scalars(select(InvestigationRunRow))}
        incident = session.get_one(IncidentRow, incident_id)
    assert runs == {"FAILED": "worker_lost", "COMPLETED": None}
    assert incident.state == "DIAGNOSED"
    assert _count(session_factory, EvidenceRow) == evidence_before + 2


async def test_r03_executor_killed_after_remote_accept(
    session_factory: sessionmaker[Session],
) -> None:
    action_id = await _queued_action(session_factory)
    adapter = FakeAdapter()
    adapter.crash_on_apply = True
    with pytest.raises(RuntimeError):
        await _executor(session_factory, adapter).execute(action_id)
    adapter.crash_on_apply = False
    assert (await _executor(session_factory, adapter).execute(action_id)).outcome is (
        ExecutionOutcome.SUCCEEDED
    )
    assert adapter.apply_calls == [action_id]


async def test_r04_api_killed_during_approval_then_duplicate_request(
    session_factory: sessionmaker[Session],
) -> None:
    incident_id = _ingest(session_factory)
    await process_one(
        session_factory,
        _runner(session_factory, ScriptedModelAdapter([lag_and_status(), submit])),
        OWNER,
        60,
    )
    with session_factory() as session:
        diagnosis_id = session.scalar(text("SELECT id FROM diagnoses"))
    outcome = await _evaluator(session_factory, FakeInspector("gen-2")).evaluate(
        incident_id, str(diagnosis_id)
    )
    with session_factory() as session:
        action = session.get_one(ActionProposalRow, outcome.action_id)

    # The process dies after writing the decision but before COMMIT.
    with pytest.raises(ProcessKilledError), session_factory.begin() as session:
        approve(session, action.id, APPROVER, action.action_hash, action.version, utcnow())
        raise ProcessKilledError
    assert _count(session_factory, ApprovalDecisionRow) == 0
    assert _count(session_factory, OutboxJobRow, OutboxJobRow.kind == EXECUTE_ACTION_JOB) == 0

    # The user retries (twice); exactly one decision and one dispatch job result.
    with session_factory.begin() as session:
        create_user(session, "approver", "lab-password-0123", HumanRole.APPROVER)
    client = TestClient(create_app(Settings(), session_factory=session_factory))
    client.post(
        "/api/v1/auth/login", json={"username": "approver", "password": "lab-password-0123"}
    )
    body = {"action_hash": action.action_hash, "expected_version": action.version}
    codes = [
        client.post(f"/api/v1/actions/{action.id}/approve", json=body).status_code for _ in range(2)
    ]
    assert codes == [200, 409]
    assert _count(session_factory, ApprovalDecisionRow) == 1
    assert _count(session_factory, OutboxJobRow, OutboxJobRow.kind == EXECUTE_ACTION_JOB) == 1


async def test_pending_approval_survives_api_restart(
    session_factory: sessionmaker[Session],
) -> None:
    # Crash point "during approval wait": the request lives only in PostgreSQL.
    action_id = await _queued_action(session_factory)  # approved via a first app
    with session_factory() as session:
        action = session.get_one(ActionProposalRow, action_id)
    assert action.state == "QUEUED"
    assert _count(session_factory, ApprovalDecisionRow) == 1  # NFR-004


async def test_r05_verifier_cannot_see_prometheus(session_factory: sessionmaker[Session]) -> None:
    action_id = await _verifying_action(session_factory)
    clock = FakeClock()
    verifier = RecoveryVerifier(
        session_factory,
        load_catalog(),
        LagBackend([], broken=True),
        FakeInspector("gen-3", paused=False),
        VerifyConfig(schema_version=1, specs={"resume_test_consumer": SPEC}),
        clock=clock,
        sleep=clock.sleep,
    )
    assert await verifier.verify(action_id) is VerificationOutcome.UNKNOWN
    with session_factory() as session:
        action = session.get_one(ActionProposalRow, action_id)
        assert session.get_one(IncidentRow, action.incident_id).state != "RESOLVED"


async def test_verifier_killed_mid_observation_is_rerun(
    session_factory: sessionmaker[Session],
) -> None:
    action_id = await _verifying_action(session_factory)

    async def killed(_: float) -> None:
        raise ProcessKilledError

    clock = FakeClock()

    def verifier(sleep: Any) -> RecoveryVerifier:
        return RecoveryVerifier(
            session_factory,
            load_catalog(),
            LagBackend([2400, 1800, 900, 400, 300]),  # not yet recovered on first look
            FakeInspector("gen-3", paused=False),
            VerifyConfig(schema_version=1, specs={"resume_test_consumer": SPEC}),
            clock=clock,
            sleep=sleep,
        )

    with pytest.raises(ProcessKilledError):
        await process_next(session_factory, verifier_worker.handlers(verifier(killed)), "v1", 60)
    assert _count(session_factory, VerificationRow) == 0
    with session_factory() as session:
        incident_id = session.get_one(ActionProposalRow, action_id).incident_id
        assert session.get_one(IncidentRow, incident_id).state == "VERIFYING"

    _expire_leases(session_factory)
    recovered = verifier(clock.sleep)
    recovered._backend = LagBackend([2400, 60, 0, 0, 0, 0])
    assert await process_next(session_factory, verifier_worker.handlers(recovered), "v2", 60)
    assert _count(session_factory, VerificationRow) == 1
    with session_factory() as session:
        assert session.get_one(IncidentRow, incident_id).state == "RESOLVED"


def test_r06_racing_executors_dispatch_once(session_factory: sessionmaker[Session]) -> None:
    import asyncio

    action_id = asyncio.run(_queued_action(session_factory))
    adapter = FakeAdapter()
    barrier = threading.Barrier(3)

    def run() -> None:
        barrier.wait()
        asyncio.run(_executor(session_factory, adapter).execute(action_id))

    threads = [threading.Thread(target=run) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert adapter.apply_calls == [action_id]


async def test_r07_timeout_before_receipt_is_reconciled(
    session_factory: sessionmaker[Session],
) -> None:
    action_id = await _queued_action(session_factory)
    adapter = FakeAdapter()
    adapter.apply_result = ApplyResult(ApplyStatus.AMBIGUOUS, detail="ReadTimeout")
    executor = _executor(session_factory, adapter)
    assert (await executor.execute(action_id)).outcome is ExecutionOutcome.RECONCILING
    adapter.lookup = LookupResult(LookupStatus.FOUND, {"status": "COMPLETED"})
    assert (await executor.reconcile(action_id)).outcome is ExecutionOutcome.SUCCEEDED
    assert adapter.apply_calls == [action_id]


async def test_r08_target_revision_changed_after_approval(
    session_factory: sessionmaker[Session],
) -> None:
    action_id = await _queued_action(session_factory)
    adapter = FakeAdapter(revision="gen-77")
    result = await _executor(session_factory, adapter).execute(action_id)
    assert (result.outcome, result.reason) == (ExecutionOutcome.FAILED, "stale_target_revision")
    assert adapter.apply_calls == []
