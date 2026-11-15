"""Incident groups across related services and the GROUPED state (ADR-030).

Adds ``incident_groups``, the group columns on ``incidents`` and the GROUPED
incident state. GROUPED is not terminal, so the active-dedup index is
unchanged. Grants are frozen here as of this revision; see
incidentops/persistence/privileges.py.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATES_BEFORE = (
    "'AWAITING_APPROVAL', 'CANCELLED', 'DIAGNOSED', 'DIAGNOSED_ONLY', 'ESCALATED', "
    "'EXECUTING', 'FAILED', 'INSUFFICIENT_EVIDENCE', 'INVESTIGATING', 'RECEIVED', "
    "'RESOLVED', 'TRIAGED', 'VERIFYING'"
)
STATES_AFTER = (
    "'AWAITING_APPROVAL', 'CANCELLED', 'DIAGNOSED', 'DIAGNOSED_ONLY', 'ESCALATED', "
    "'EXECUTING', 'FAILED', 'GROUPED', 'INSUFFICIENT_EVIDENCE', 'INVESTIGATING', "
    "'RECEIVED', 'RESOLVED', 'TRIAGED', 'VERIFYING'"
)
GRANTS = [
    "GRANT INSERT, SELECT, UPDATE ON TABLE incident_groups TO incidentops_api",
    "GRANT SELECT ON TABLE incident_groups TO incidentops_agent",
    "GRANT SELECT ON TABLE incident_groups TO incidentops_executor",
    "GRANT SELECT ON TABLE incident_groups TO incidentops_verifier",
    "GRANT SELECT ON TABLE alert_events TO incidentops_verifier",
    "GRANT INSERT ON TABLE outbox_jobs TO incidentops_verifier",
]
REVOKES = [
    "REVOKE INSERT ON TABLE outbox_jobs FROM incidentops_verifier",
    "REVOKE SELECT ON TABLE alert_events FROM incidentops_verifier",
]


def upgrade() -> None:
    op.create_table(
        "incident_groups",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column("primary_incident_id", sa.String(length=40), nullable=False),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("window_ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["primary_incident_id"],
            ["incidents.id"],
            name=op.f("fk_incident_groups_primary_incident_id_incidents"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incident_groups")),
    )
    op.create_index(
        "ix_incident_groups_environment",
        "incident_groups",
        ["environment", "window_ends_at"],
        unique=False,
    )
    op.add_column("incidents", sa.Column("group_id", sa.String(length=40), nullable=True))
    op.add_column(
        "incidents", sa.Column("group_split_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("incidents", sa.Column("group_note", sa.String(length=500), nullable=True))
    op.add_column("incidents", sa.Column("symptom_class", sa.String(length=16), nullable=True))
    op.create_foreign_key(
        op.f("fk_incidents_group_id_incident_groups"),
        "incidents",
        "incident_groups",
        ["group_id"],
        ["id"],
    )
    op.create_index(op.f("ix_incidents_group_id"), "incidents", ["group_id"], unique=False)
    op.create_check_constraint(
        "symptom_class_valid",
        "incidents",
        "symptom_class IS NULL OR symptom_class IN ('intrinsic', 'dependency')",
    )
    op.drop_constraint(op.f("ck_incidents_state_valid"), "incidents", type_="check")
    op.create_check_constraint("state_valid", "incidents", f"state IN ({STATES_AFTER})")
    for statement in GRANTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in REVOKES:
        op.execute(statement)
    # A grouped incident has no pre-0012 meaning: release it for investigation.
    op.execute("UPDATE incidents SET state = 'RECEIVED' WHERE state = 'GROUPED'")
    op.drop_constraint(op.f("ck_incidents_state_valid"), "incidents", type_="check")
    op.create_check_constraint("state_valid", "incidents", f"state IN ({STATES_BEFORE})")
    op.drop_constraint(op.f("ck_incidents_symptom_class_valid"), "incidents", type_="check")
    op.drop_index(op.f("ix_incidents_group_id"), table_name="incidents")
    op.drop_constraint(
        op.f("fk_incidents_group_id_incident_groups"), "incidents", type_="foreignkey"
    )
    op.drop_column("incidents", "symptom_class")
    op.drop_column("incidents", "group_note")
    op.drop_column("incidents", "group_split_at")
    op.drop_column("incidents", "group_id")
    op.drop_index("ix_incident_groups_environment", table_name="incident_groups")
    op.drop_table("incident_groups")
