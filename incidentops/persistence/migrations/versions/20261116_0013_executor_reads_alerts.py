"""Let the executor read alert events (ADR-030, fix A).

When an action fails and the executor escalates a group primary, members whose
alerts are still firing are split out for their own investigation; deciding
that reads the members' alert events in the same transaction.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("GRANT SELECT ON TABLE alert_events TO incidentops_executor")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON TABLE alert_events FROM incidentops_executor")
