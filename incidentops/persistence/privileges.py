"""Least-privilege database roles per process (P4, architecture §15).

This matrix is the single source of truth for table privileges. Migrations
apply it with explicit GRANT statements, and an integration test compares the
live catalog with it. No role except the migration owner may UPDATE or DELETE
audit events, and the agent role cannot touch actions, approvals, executions,
verifications or user accounts.
"""

from __future__ import annotations

from typing import Final

API_ROLE: Final = "incidentops_api"
AGENT_ROLE: Final = "incidentops_agent"
EXECUTOR_ROLE: Final = "incidentops_executor"
VERIFIER_ROLE: Final = "incidentops_verifier"
ROLES: Final = (API_ROLE, AGENT_ROLE, EXECUTOR_ROLE, VERIFIER_ROLE)
LANGGRAPH_SCHEMA: Final = "langgraph"

S, I, U = "SELECT", "INSERT", "UPDATE"  # noqa: E741 - short privilege names

PRIVILEGES: Final[dict[str, dict[str, frozenset[str]]]] = {
    # API process, control worker (proposal evaluation, expiry) and admin CLI.
    API_ROLE: {
        "incidents": frozenset({S, I, U}),
        "alert_events": frozenset({S, I}),
        "investigation_runs": frozenset({S}),
        "agent_steps": frozenset({S}),
        "evidence": frozenset({S}),
        "diagnoses": frozenset({S}),
        "action_proposals": frozenset({S, I, U}),
        "approval_requests": frozenset({S, I, U}),
        "approval_decisions": frozenset({S, I}),
        "external_operations": frozenset({S}),
        "verifications": frozenset({S}),
        "audit_events": frozenset({S, I}),
        "outbox_jobs": frozenset({S, I, U}),
        "policy_versions": frozenset({S}),
        "users": frozenset({S, I}),
        "sessions": frozenset({S, I, U}),
        "incident_chat_messages": frozenset({S, I}),
    },
    # Investigation worker: its own run, steps, evidence and diagnosis, plus the
    # incident lifecycle up to DIAGNOSED. No action/approval/execution writes.
    AGENT_ROLE: {
        "incidents": frozenset({S, U}),
        "alert_events": frozenset({S}),
        "investigation_runs": frozenset({S, I, U}),
        "agent_steps": frozenset({S, I}),
        "evidence": frozenset({S, I}),
        "diagnoses": frozenset({S, I}),
        "audit_events": frozenset({I}),
        "outbox_jobs": frozenset({S, I, U}),
        # Answers to the incident chat; questions are written by the API.
        "incident_chat_messages": frozenset({S, I}),
    },
    # Executor: action execution state, external operations, follow-up jobs.
    EXECUTOR_ROLE: {
        "incidents": frozenset({S, U}),
        "action_proposals": frozenset({S, U}),
        "approval_requests": frozenset({S}),
        "external_operations": frozenset({S, I, U}),
        "audit_events": frozenset({I}),
        "outbox_jobs": frozenset({S, I, U}),
        "policy_versions": frozenset({S}),
    },
    # Verifier: reads execution results, writes verifications and resolution.
    VERIFIER_ROLE: {
        "incidents": frozenset({S, U}),
        "action_proposals": frozenset({S}),
        "external_operations": frozenset({S}),
        "verifications": frozenset({S, I}),
        "audit_events": frozenset({I}),
        "outbox_jobs": frozenset({S, U}),
    },
}


def grant_statements() -> list[str]:
    """SQL that applies the matrix (used by migrations and test setup)."""
    statements: list[str] = []
    for role, tables in PRIVILEGES.items():
        for table, privileges in sorted(tables.items()):
            joined = ", ".join(sorted(privileges))
            statements.append(f"GRANT {joined} ON TABLE {table} TO {role}")
        audit = tables.get("audit_events", frozenset())
        if I in audit:
            # The identity column's sequence is needed to insert audit events,
            # and the ORM reads the generated key back with RETURNING, which
            # needs SELECT on those generated columns only.
            statements.append(f"GRANT USAGE ON SEQUENCE audit_events_seq_seq TO {role}")
            if S not in audit:
                statements.append(
                    f"GRANT SELECT (seq, occurred_at) ON TABLE audit_events TO {role}"
                )
    return statements
