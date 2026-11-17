"""A schema for the lakehouse's Iceberg catalog.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-17

S2's Iceberg catalog is PyIceberg's SQL catalog (ADR 0011): two small tables listing
namespaces and each table's current metadata file. Data and metadata files live in the
object store. The tables are created by PyIceberg itself on first use; this migration
gives them a schema of their own, owned by nothing in the ontology, which the pipeline
service (connecting as s2_app) may create tables in.

The catalog is deployment-wide, not per tenant: ADR 0011 says how that changes.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _run(sql: str) -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(sql)


def upgrade() -> None:
    _run("CREATE SCHEMA IF NOT EXISTS lakehouse")
    _run("GRANT USAGE, CREATE ON SCHEMA lakehouse TO s2_app")


def downgrade() -> None:
    # Fails, deliberately, while the catalog still lists tables: dropping it would orphan
    # every table's files in the object store.
    _run("REVOKE ALL ON SCHEMA lakehouse FROM s2_app")
    _run("DROP SCHEMA IF EXISTS lakehouse")
