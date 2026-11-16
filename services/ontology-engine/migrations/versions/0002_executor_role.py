"""The action executor's database role.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-17

`s2_executor` delivers write-backs for every tenant, so it must see every tenant's outbox
rows: it has BYPASSRLS. Everything else about it is narrow:

* the outbox: read, and update only the columns delivery changes;
* the audit log: read and append (the chain trigger needs to read the previous entry);
* nothing else. It cannot read objects or action parameters, and cannot touch the
  ontology definitions.

The Compose stack creates the role with a password before migrating (initdb), as it does
for `s2_app`; elsewhere this creates it without login and the operator grants access.
Setting BYPASSRLS needs a superuser, so this migration must run as one (managed
PostgreSQL offerings that withhold it need the role created by the provider first).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _run(sql: str) -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(sql)


STATEMENTS: list[str] = [
    """\
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 's2_executor') THEN
        CREATE ROLE s2_executor NOLOGIN;
    END IF;
END;
$$
""",
    "ALTER ROLE s2_executor BYPASSRLS NOSUPERUSER",
    "GRANT USAGE ON SCHEMA ontology TO s2_executor",
    "GRANT SELECT ON ontology.writeback_outbox TO s2_executor",
    "GRANT UPDATE (status, attempts, next_attempt_at, delivered_at, last_error)"
    " ON ontology.writeback_outbox TO s2_executor",
    "GRANT SELECT, INSERT ON ontology.audit_log TO s2_executor",
    # audit_chain() and audit_entry_hash() run in the inserting role's context.
    "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA ontology TO s2_executor",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        _run(statement)


def downgrade() -> None:
    _run("REVOKE ALL ON ontology.writeback_outbox, ontology.audit_log FROM s2_executor")
    _run("REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA ontology FROM s2_executor")
    _run("REVOKE USAGE ON SCHEMA ontology FROM s2_executor")
    # The role itself is left in place, like s2_app in 0001.
