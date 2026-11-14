"""Count top-level report fields moved out of diagnosis (ADR-028).

Models sometimes put needs_human_review or proposed_action inside the
diagnosis object of submit_diagnosis. The harness moves such a field up when
that is unambiguous; this column records how often it happened.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "investigation_runs",
        sa.Column("field_lifts", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("investigation_runs", "field_lifts")
