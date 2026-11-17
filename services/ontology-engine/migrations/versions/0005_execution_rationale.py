"""Why an action was requested: free text, required from agents.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql("ALTER TABLE ontology.action_execution ADD COLUMN rationale TEXT")


def downgrade() -> None:
    op.get_bind().exec_driver_sql("ALTER TABLE ontology.action_execution DROP COLUMN rationale")
