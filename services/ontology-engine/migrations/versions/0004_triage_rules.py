"""Triage rules: conditions over an object type that turn matching objects into a work queue.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-17

Defined in ontology packs, like object, link and action types, and isolated by tenant
the same way (ADR 0007). The executor role has no access to them.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _run(sql: str) -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(sql)


STATEMENTS = [
    """\
CREATE TABLE ontology.triage_rule (
    id UUID DEFAULT uuidv7() NOT NULL,
    api_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    description TEXT,
    object_type_id UUID NOT NULL,
    severity VARCHAR(16) DEFAULT 'medium' NOT NULL,
    conditions JSONB DEFAULT '[]'::jsonb NOT NULL,
    actions TEXT[] DEFAULT '{}'::text[] NOT NULL,
    status VARCHAR(32) DEFAULT 'active' NOT NULL,
    version INTEGER DEFAULT 1 NOT NULL,
    tenant_id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    created_by TEXT NOT NULL,
    CONSTRAINT pk_triage_rule PRIMARY KEY (id),
    CONSTRAINT uq_triage_rule_tenant_id_api_name UNIQUE (tenant_id, api_name),
    CONSTRAINT ck_triage_rule_api_name_camel CHECK (api_name ~ '^[a-z][A-Za-z0-9]{0,62}$'),
    CONSTRAINT ck_triage_rule_severity CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    CONSTRAINT ck_triage_rule_status CHECK (status IN ('draft', 'active', 'deprecated')),
    CONSTRAINT fk_triage_rule_object_type_id_object_type FOREIGN KEY (object_type_id)
        REFERENCES ontology.object_type (id) ON DELETE RESTRICT
)""",
    "CREATE INDEX ix_triage_rule_tenant_id ON ontology.triage_rule (tenant_id)",
    "ALTER TABLE ontology.triage_rule ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE ontology.triage_rule FORCE ROW LEVEL SECURITY",
    """\
CREATE POLICY tenant_isolation ON ontology.triage_rule
    USING      (tenant_id = NULLIF(current_setting('s2.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('s2.tenant_id', true), '')::uuid)""",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON ontology.triage_rule TO s2_app",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        _run(statement)


def downgrade() -> None:
    _run("DROP TABLE ontology.triage_rule")
