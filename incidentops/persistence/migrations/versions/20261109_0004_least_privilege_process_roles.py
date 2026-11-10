"""Least-privilege group roles per process (P4).

Creates NOLOGIN group roles, grants the table privileges from
``incidentops/persistence/privileges.py`` (frozen here as of this revision)
and a ``langgraph`` schema in which only the agent role may create the
LangGraph checkpoint tables. Login users are created per environment (see
``scripts/create_db_logins.py``) as members of these roles.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLES = ("incidentops_api", "incidentops_agent", "incidentops_executor", "incidentops_verifier")

GRANTS = [
    "GRANT INSERT, SELECT, UPDATE ON TABLE action_proposals TO incidentops_api",
    "GRANT SELECT ON TABLE agent_steps TO incidentops_api",
    "GRANT INSERT, SELECT ON TABLE alert_events TO incidentops_api",
    "GRANT INSERT, SELECT ON TABLE approval_decisions TO incidentops_api",
    "GRANT INSERT, SELECT, UPDATE ON TABLE approval_requests TO incidentops_api",
    "GRANT INSERT, SELECT ON TABLE audit_events TO incidentops_api",
    "GRANT SELECT ON TABLE diagnoses TO incidentops_api",
    "GRANT SELECT ON TABLE evidence TO incidentops_api",
    "GRANT SELECT ON TABLE external_operations TO incidentops_api",
    "GRANT INSERT, SELECT, UPDATE ON TABLE incidents TO incidentops_api",
    "GRANT SELECT ON TABLE investigation_runs TO incidentops_api",
    "GRANT INSERT, SELECT, UPDATE ON TABLE outbox_jobs TO incidentops_api",
    "GRANT SELECT ON TABLE policy_versions TO incidentops_api",
    "GRANT INSERT, SELECT, UPDATE ON TABLE sessions TO incidentops_api",
    "GRANT INSERT, SELECT ON TABLE users TO incidentops_api",
    "GRANT SELECT ON TABLE verifications TO incidentops_api",
    "GRANT USAGE ON SEQUENCE audit_events_seq_seq TO incidentops_api",
    "GRANT INSERT, SELECT ON TABLE agent_steps TO incidentops_agent",
    "GRANT SELECT ON TABLE alert_events TO incidentops_agent",
    "GRANT INSERT ON TABLE audit_events TO incidentops_agent",
    "GRANT INSERT, SELECT ON TABLE diagnoses TO incidentops_agent",
    "GRANT INSERT, SELECT ON TABLE evidence TO incidentops_agent",
    "GRANT SELECT, UPDATE ON TABLE incidents TO incidentops_agent",
    "GRANT INSERT, SELECT, UPDATE ON TABLE investigation_runs TO incidentops_agent",
    "GRANT INSERT, SELECT, UPDATE ON TABLE outbox_jobs TO incidentops_agent",
    "GRANT USAGE ON SEQUENCE audit_events_seq_seq TO incidentops_agent",
    "GRANT SELECT (seq, occurred_at) ON TABLE audit_events TO incidentops_agent",
    "GRANT SELECT, UPDATE ON TABLE action_proposals TO incidentops_executor",
    "GRANT SELECT ON TABLE approval_requests TO incidentops_executor",
    "GRANT INSERT ON TABLE audit_events TO incidentops_executor",
    "GRANT INSERT, SELECT, UPDATE ON TABLE external_operations TO incidentops_executor",
    "GRANT SELECT, UPDATE ON TABLE incidents TO incidentops_executor",
    "GRANT INSERT, SELECT, UPDATE ON TABLE outbox_jobs TO incidentops_executor",
    "GRANT SELECT ON TABLE policy_versions TO incidentops_executor",
    "GRANT USAGE ON SEQUENCE audit_events_seq_seq TO incidentops_executor",
    "GRANT SELECT (seq, occurred_at) ON TABLE audit_events TO incidentops_executor",
    "GRANT SELECT ON TABLE action_proposals TO incidentops_verifier",
    "GRANT INSERT ON TABLE audit_events TO incidentops_verifier",
    "GRANT SELECT ON TABLE external_operations TO incidentops_verifier",
    "GRANT SELECT, UPDATE ON TABLE incidents TO incidentops_verifier",
    "GRANT SELECT, UPDATE ON TABLE outbox_jobs TO incidentops_verifier",
    "GRANT INSERT, SELECT ON TABLE verifications TO incidentops_verifier",
    "GRANT USAGE ON SEQUENCE audit_events_seq_seq TO incidentops_verifier",
    "GRANT SELECT (seq, occurred_at) ON TABLE audit_events TO incidentops_verifier",
]


def upgrade() -> None:
    for role in ROLES:
        # Roles are cluster-wide: create them only when missing.
        op.execute(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') "
            f"THEN CREATE ROLE {role} NOLOGIN; END IF; END $$"
        )
        op.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
    for statement in GRANTS:
        op.execute(statement)
    op.execute("CREATE SCHEMA IF NOT EXISTS langgraph")
    op.execute("GRANT USAGE, CREATE ON SCHEMA langgraph TO incidentops_agent")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS langgraph CASCADE")
    for role in ROLES:
        # Roles may be shared by other databases in the cluster, so they are kept;
        # only this database's privileges are revoked.
        op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}")
        op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role}")
        op.execute(f"REVOKE ALL ON SCHEMA public FROM {role}")
