"""Chat agent runs, chat summaries and human-requested re-investigations.

- ``investigation_runs.kind`` separates formal investigations from chat turns
  and re-investigations; at most one chat turn and one re-investigation can be
  open per incident at a time.
- Chat answers record the run that produced them and the diagnosis they were
  based on.
- ``incident_chat_summaries`` holds the rolling summary of older chat turns.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNS = "investigation_runs"
CHAT = "incident_chat_messages"
SUMMARIES = "incident_chat_summaries"
OPEN = "state IN ('QUEUED', 'RUNNING')"
# Frozen as of this revision; see incidentops/persistence/privileges.py.
GRANTS = [
    f"GRANT SELECT ON TABLE {SUMMARIES} TO incidentops_api",
    f"GRANT INSERT, SELECT ON TABLE {SUMMARIES} TO incidentops_agent",
]


def upgrade() -> None:
    op.add_column(
        RUNS,
        sa.Column("kind", sa.String(length=16), server_default="investigation", nullable=False),
    )
    op.create_check_constraint(
        "kind_valid", RUNS, "kind IN ('investigation', 'reinvestigation', 'chat')"
    )
    op.add_column(RUNS, sa.Column("requested_by", sa.String(length=128), nullable=True))
    op.create_index(
        "uq_investigation_runs_one_open_chat",
        RUNS,
        ["incident_id"],
        unique=True,
        postgresql_where=sa.text(f"kind = 'chat' AND {OPEN}"),
    )
    op.create_index(
        "uq_investigation_runs_one_open_reinvestigation",
        RUNS,
        ["incident_id"],
        unique=True,
        postgresql_where=sa.text(f"kind = 'reinvestigation' AND {OPEN}"),
    )

    op.add_column(CHAT, sa.Column("run_id", sa.String(length=40), nullable=True))
    op.add_column(CHAT, sa.Column("diagnosis_id", sa.String(length=40), nullable=True))
    op.create_foreign_key(f"fk_{CHAT}_run_id_{RUNS}", CHAT, RUNS, ["run_id"], ["id"])
    op.create_foreign_key(
        f"fk_{CHAT}_diagnosis_id_diagnoses", CHAT, "diagnoses", ["diagnosis_id"], ["id"]
    )

    op.create_table(
        SUMMARIES,
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("incident_id", sa.String(length=40), nullable=False),
        sa.Column("through_message_id", sa.String(length=40), nullable=False),
        sa.Column("covered_messages", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("author", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "octet_length(content) <= 4000", name=op.f(f"ck_{SUMMARIES}_content_budget")
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"], ["incidents.id"], name=op.f(f"fk_{SUMMARIES}_incident_id_incidents")
        ),
        sa.ForeignKeyConstraint(
            ["through_message_id"],
            [f"{CHAT}.id"],
            name=op.f(f"fk_{SUMMARIES}_through_message_id_{CHAT}"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{SUMMARIES}")),
        sa.UniqueConstraint("through_message_id", name=op.f(f"uq_{SUMMARIES}_through_message")),
    )
    op.create_index(f"ix_{SUMMARIES}_incident", SUMMARIES, ["incident_id", "created_at"])
    for statement in GRANTS:
        op.execute(statement)


def downgrade() -> None:
    op.drop_index(f"ix_{SUMMARIES}_incident", table_name=SUMMARIES)
    op.drop_table(SUMMARIES)
    op.drop_constraint(f"fk_{CHAT}_diagnosis_id_diagnoses", CHAT, type_="foreignkey")
    op.drop_constraint(f"fk_{CHAT}_run_id_{RUNS}", CHAT, type_="foreignkey")
    op.drop_column(CHAT, "diagnosis_id")
    op.drop_column(CHAT, "run_id")
    op.drop_index("uq_investigation_runs_one_open_reinvestigation", table_name=RUNS)
    op.drop_index("uq_investigation_runs_one_open_chat", table_name=RUNS)
    op.drop_column(RUNS, "requested_by")
    op.drop_constraint("kind_valid", RUNS, type_="check")
    op.drop_column(RUNS, "kind")
