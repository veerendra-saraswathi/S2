"""Objects an action's policy sees beside its target: `policy_context`.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE ontology.action_type ADD COLUMN policy_context JSONB DEFAULT '{}'::jsonb NOT NULL"
    )


def downgrade() -> None:
    op.get_bind().exec_driver_sql("ALTER TABLE ontology.action_type DROP COLUMN policy_context")
