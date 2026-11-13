"""Record cached input tokens per investigation run.

``input_tokens`` counts uncached input only. Providers report prompt-cache
reads and writes separately (and bill them differently), so without these
columns the recorded input understated what a run consumed.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "investigation_runs",
        sa.Column("cache_read_input_tokens", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "investigation_runs",
        sa.Column("cache_creation_input_tokens", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("investigation_runs", "cache_creation_input_tokens")
    op.drop_column("investigation_runs", "cache_read_input_tokens")
