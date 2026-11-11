"""Shared, read-only chat per incident.

Questions are written by the API, answers by the investigation (agent)
worker. Neither role may update or delete a message.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "incident_chat_messages"
# Frozen as of this revision; see incidentops/persistence/privileges.py.
GRANTS = [
    f"GRANT INSERT, SELECT ON TABLE {TABLE} TO incidentops_api",
    f"GRANT INSERT, SELECT ON TABLE {TABLE} TO incidentops_agent",
]


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("incident_id", sa.String(length=40), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("author", sa.String(length=128), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("reply_to", sa.String(length=40), nullable=True),
        sa.Column("cited_evidence_ids", postgresql.JSONB(), nullable=False),
        sa.Column("records_checked", sa.Integer(), server_default="0", nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("suggest_reinvestigation", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('user', 'assistant')", name=op.f(f"ck_{TABLE}_role_valid")),
        sa.CheckConstraint(
            "octet_length(content) <= 8000", name=op.f(f"ck_{TABLE}_content_budget")
        ),
        sa.CheckConstraint(
            "(role = 'user') = (reply_to IS NULL)",
            name=op.f(f"ck_{TABLE}_answers_reply_to_a_question"),
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"], ["incidents.id"], name=op.f(f"fk_{TABLE}_incident_id_incidents")
        ),
        sa.ForeignKeyConstraint(
            ["reply_to"], [f"{TABLE}.id"], name=op.f(f"fk_{TABLE}_reply_to_{TABLE}")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
        sa.UniqueConstraint("reply_to", name=op.f(f"uq_{TABLE}_one_answer")),
    )
    op.create_index(f"ix_{TABLE}_incident", TABLE, ["incident_id", "created_at"], unique=False)
    for statement in GRANTS:
        op.execute(statement)


def downgrade() -> None:
    op.drop_index(f"ix_{TABLE}_incident", table_name=TABLE)
    op.drop_table(TABLE)
