"""Least-privilege database roles (P4): catalog matches the matrix, and each
process role can do exactly its job and nothing more."""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from incidentops.persistence.privileges import PRIVILEGES, ROLES

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "role-password-0123"


def _logins_module() -> ModuleType:
    path = REPO_ROOT / "scripts" / "create_db_logins.py"
    spec = importlib.util.spec_from_file_location("create_db_logins", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def role_engines(engine: Engine, database_url: str) -> Iterator[dict[str, Engine]]:
    module = _logins_module()
    engines: dict[str, Engine] = {}
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for role in ROLES:
            login = f"io_test_{role.removeprefix('incidentops_')}"
            module.ensure_login(conn, login, role, PASSWORD)
            url = make_url(database_url).set(username=login, password=PASSWORD)
            engines[role] = create_engine(url)
    yield engines
    for role_engine in engines.values():
        role_engine.dispose()


def test_catalog_grants_match_the_matrix(engine: Engine) -> None:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT grantee, table_name, privilege_type "
                "FROM information_schema.role_table_grants "
                "WHERE table_schema = 'public' AND grantee = ANY(:roles)"
            ),
            {"roles": list(ROLES)},
        ).all()
    actual: dict[str, dict[str, set[str]]] = {role: {} for role in ROLES}
    for grantee, table, privilege in rows:
        actual[grantee].setdefault(table, set()).add(privilege)
    expected = {role: {t: set(p) for t, p in tables.items()} for role, tables in PRIVILEGES.items()}
    assert actual == expected


@pytest.mark.parametrize(
    ("role", "statement"),
    [
        # The agent cannot touch actions, approvals, executions or accounts.
        ("incidentops_agent", "UPDATE action_proposals SET state = 'APPROVED'"),
        (
            "incidentops_agent",
            "INSERT INTO approval_decisions (id, approval_id, approver_id, "
            "decision, action_hash) VALUES ('d', 'a', 'x', 'APPROVED', 'h')",
        ),
        ("incidentops_agent", "SELECT password_hash FROM users"),
        ("incidentops_agent", "UPDATE external_operations SET status = 'COMPLETED'"),
        # The executor cannot fabricate diagnoses, approvals or verifications.
        (
            "incidentops_executor",
            "INSERT INTO diagnoses (id, run_id, root_cause_id, report, "
            "cited_evidence_ids) VALUES ('d', 'r', 'x', '{}', '[]')",
        ),
        ("incidentops_executor", "UPDATE approval_requests SET state = 'APPROVED'"),
        (
            "incidentops_executor",
            "INSERT INTO verifications (id, action_id, outcome, checks, "
            "window_start, window_end, evidence_ids) VALUES "
            "('v', 'a', 'PASSED', '[]', now(), now(), '[]')",
        ),
        ("incidentops_executor", "SELECT * FROM sessions"),
        # The verifier cannot change actions or approvals.
        ("incidentops_verifier", "UPDATE action_proposals SET state = 'SUCCEEDED'"),
        ("incidentops_verifier", "UPDATE approval_requests SET state = 'APPROVED'"),
        # Nobody but the owner can rewrite or delete audit history, or delete rows.
        ("incidentops_api", "UPDATE audit_events SET actor = 'mallory'"),
        ("incidentops_api", "DELETE FROM audit_events"),
        ("incidentops_api", "DELETE FROM incidents"),
        ("incidentops_agent", "TRUNCATE audit_events"),
        ("incidentops_agent", "SELECT actor, reason FROM audit_events"),
    ],
)
def test_out_of_role_statements_are_refused(
    role_engines: dict[str, Engine], role: str, statement: str
) -> None:
    with (
        pytest.raises((ProgrammingError, DBAPIError), match="permission denied"),
        role_engines[role].begin() as conn,
    ):
        conn.execute(text(statement))


async def test_closed_loop_runs_with_least_privilege_roles(
    role_engines: dict[str, Engine], session_factory: sessionmaker[Session]
) -> None:
    """Each stage runs with only its own role's database login."""
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from incidentops.actions.proposals import ProposalEvaluator
    from incidentops.api.app import create_app
    from incidentops.auth.service import HumanRole, create_user
    from incidentops.config import Settings
    from incidentops.domain.catalog import load_catalog
    from incidentops.execution.executor import ActionExecutor
    from incidentops.persistence.models import ActionProposalRow, IncidentRow
    from incidentops.policy.engine import PolicyEngine, load_policy
    from incidentops.verification.verifier import RecoveryVerifier, VerifyConfig
    from incidentops.workers import control, executor, verifier
    from incidentops.workers.common import process_next
    from tests.integration.helpers import FakeAdapter, FakeInspector, diagnosed_incident
    from tests.integration.test_verifier import SPEC, FakeClock, LagBackend

    def factory(role: str) -> sessionmaker[Session]:
        return sessionmaker(bind=role_engines[role], expire_on_commit=False)

    api, agent = factory("incidentops_api"), factory("incidentops_agent")
    catalog = load_catalog()
    policy = PolicyEngine(load_policy(), catalog)

    # Ingestion (API role) and investigation (agent role).
    from incidentops.agent.model import ScriptedModelAdapter
    from incidentops.workers.investigation import process_one
    from tests.integration.test_investigation_worker import OWNER, _ingest, _runner
    from tests.unit.test_investigation_graph import lag_and_status, submit

    incident_id = _ingest(api)
    await process_one(
        agent, _runner(agent, ScriptedModelAdapter([lag_and_status(), submit])), OWNER, 60
    )

    # Proposal evaluation and approval (API role).
    evaluator = ProposalEvaluator(api, policy, FakeInspector("gen-2"))
    assert await process_next(api, control.handlers(evaluator), "control", 60)
    with session_factory.begin() as session:
        create_user(session, "role-approver", "lab-password-0123", HumanRole.APPROVER)
        action = session.scalars(select(ActionProposalRow)).one()
    client = TestClient(create_app(Settings(), session_factory=api))
    client.post(
        "/api/v1/auth/login", json={"username": "role-approver", "password": "lab-password-0123"}
    )
    decision = client.post(
        f"/api/v1/actions/{action.id}/approve",
        json={"action_hash": action.action_hash, "expected_version": action.version},
    )
    assert decision.status_code == 200, decision.text

    # Execution (executor role) and verification (verifier role).
    exec_factory = factory("incidentops_executor")
    adapter = FakeAdapter("gen-2")
    handlers = executor.handlers(
        ActionExecutor(exec_factory, policy, {"resume_test_consumer": adapter})
    )
    assert await process_next(exec_factory, handlers, "executor", 60)
    ver_factory = factory("incidentops_verifier")
    clock = FakeClock()
    recovery = RecoveryVerifier(
        ver_factory,
        catalog,
        LagBackend([2400, 60, 0, 0, 0, 0]),
        FakeInspector("gen-3", paused=False),
        VerifyConfig(schema_version=1, specs={"resume_test_consumer": SPEC}),
        clock=clock,
        sleep=clock.sleep,
    )
    assert await process_next(ver_factory, verifier.handlers(recovery), "verifier", 60)
    assert adapter.apply_calls == [action.id]
    with session_factory() as session:
        assert session.get_one(IncidentRow, incident_id).state == "RESOLVED"
    _ = diagnosed_incident  # imported helper kept for symmetry with other loop tests


async def test_agent_owns_checkpoints_only_in_langgraph_schema(
    role_engines: dict[str, Engine],
) -> None:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    from incidentops.persistence.urls import checkpoint_conninfo

    agent_url = role_engines["incidentops_agent"].url.render_as_string(hide_password=False)
    async with AsyncPostgresSaver.from_conn_string(checkpoint_conninfo(agent_url)) as saver:
        await saver.setup()
    with role_engines["incidentops_agent"].connect() as conn:
        schemas = set(
            conn.execute(
                text(
                    "SELECT table_schema FROM information_schema.tables "
                    "WHERE table_name = 'checkpoints'"
                )
            ).scalars()
        )
    assert "langgraph" in schemas
    # The agent may not create tables in the main schema.
    with (
        pytest.raises((ProgrammingError, DBAPIError), match="permission denied"),
        role_engines["incidentops_agent"].begin() as conn,
    ):
        conn.execute(text("CREATE TABLE public.agent_scratch (id int)"))


async def test_chat_and_reinvestigation_run_with_the_agent_role(
    role_engines: dict[str, Engine], session_factory: sessionmaker[Session]
) -> None:
    """The chat worker and re-investigation read the full incident snapshot as the agent."""
    from sqlalchemy import select, update

    from incidentops.actions.proposals import ProposalEvaluator
    from incidentops.agent.model import ScriptedModelAdapter
    from incidentops.auth.service import AuthenticatedUser, HumanRole
    from incidentops.chat import service as chat
    from incidentops.chat.answerer import ChatAnswerer
    from incidentops.domain.catalog import load_catalog
    from incidentops.persistence.models import IncidentChatMessageRow, IncidentRow
    from incidentops.policy.engine import PolicyEngine, load_policy
    from incidentops.workers import control
    from incidentops.workers.chat import process_chat_one
    from incidentops.workers.common import process_next
    from incidentops.workers.investigation import process_reinvestigation_one
    from tests.integration.helpers import FakeInspector, diagnosed_incident
    from tests.integration.test_investigation_worker import _runner
    from tests.unit.test_investigation_graph import lag_and_status, submit

    agent = sessionmaker(bind=role_engines["incidentops_agent"], expire_on_commit=False)
    incident_id, _ = await diagnosed_incident(session_factory)
    # An action proposal exists, so the snapshot reads action_proposals.
    evaluator = ProposalEvaluator(
        session_factory, PolicyEngine(load_policy(), load_catalog()), FakeInspector("gen-2")
    )
    assert await process_next(session_factory, control.handlers(evaluator), "control", 60)
    operator = AuthenticatedUser("u1", "user:omar", HumanRole.OPERATOR)
    with session_factory.begin() as session:
        session.execute(
            update(IncidentRow).where(IncidentRow.id == incident_id).values(state="ESCALATED")
        )
        question = chat.ask(session, incident_id, operator, "What should I check?")
    assert question is not None

    assert await process_chat_one(agent, ChatAnswerer(agent, None), "w", 30)
    with session_factory() as session:
        answer = session.scalars(
            select(IncidentChatMessageRow).where(IncidentChatMessageRow.reply_to == question.id)
        ).one()
    assert answer.run_id is not None

    with session_factory.begin() as session:
        assert chat.request_reinvestigation(session, incident_id, operator, "check", None) == (
            chat.ReinvestigationOutcome.QUEUED
        )
    runner = _runner(agent, ScriptedModelAdapter([lag_and_status(), submit]))
    assert await process_reinvestigation_one(agent, runner, "w", 60)
    with session_factory() as session:
        assert chat.reinvestigation_status(session, incident_id).state == "COMPLETED"  # type: ignore[union-attr]
