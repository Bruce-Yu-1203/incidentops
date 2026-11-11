"""Human ownership and closing disposition on handed-over incidents (ADR-021).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen as of this revision; see incidentops/domain/disposition.py.
CHECK = (
    "disposition IS NULL OR (disposition IN ('FIXED_MANUALLY', 'RECOVERED_ON_ITS_OWN', "
    "'FALSE_ALARM', 'WONT_FIX') AND state IN ('DIAGNOSED_ONLY', 'ESCALATED', 'FAILED') "
    "AND disposed_by IS NOT NULL AND disposed_at IS NOT NULL)"
)


def upgrade() -> None:
    op.add_column("incidents", sa.Column("owner_username", sa.String(64), nullable=True))
    op.add_column("incidents", sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("incidents", sa.Column("disposition", sa.String(32), nullable=True))
    op.add_column("incidents", sa.Column("disposition_note", sa.String(2000), nullable=True))
    op.add_column("incidents", sa.Column("disposed_by", sa.String(64), nullable=True))
    op.add_column("incidents", sa.Column("disposed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint("disposition_valid", "incidents", CHECK)


def downgrade() -> None:
    op.drop_constraint("disposition_valid", "incidents", type_="check")
    for column in (
        "disposed_at",
        "disposed_by",
        "disposition_note",
        "disposition",
        "assigned_at",
        "owner_username",
    ):
        op.drop_column("incidents", column)
