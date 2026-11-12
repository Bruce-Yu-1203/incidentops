"""Let the agent role read actions and verifications.

The chat agent's incident snapshot (ADR-023) lists the proposed actions and
their verifications. Migration 0004 granted those tables only to the API,
executor and verifier, so every chat turn failed with "permission denied".
The agent gets SELECT only: it still cannot write actions, approvals,
executions or verifications.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Frozen as of this revision; see incidentops/persistence/privileges.py.
    op.execute("GRANT SELECT ON TABLE action_proposals, verifications TO incidentops_agent")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON TABLE action_proposals, verifications FROM incidentops_agent")
