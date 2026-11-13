"""Count diagnosis fields that arrived as JSON strings and were parsed.

Some providers (DeepSeek) occasionally send the nested ``diagnosis`` or
``proposed_action`` object of submit_diagnosis as a JSON-encoded string. The
harness parses such strings; this column records how often that happened.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "investigation_runs",
        sa.Column("argument_coercions", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("investigation_runs", "argument_coercions")
