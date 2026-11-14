"""Ontology kernel: metamodel, runtime tables, audit chain, tenant isolation.

Revision ID: 0001
Revises:
Create Date: 2026-11-14

The CREATE TABLE / CREATE INDEX statements were compiled from s2_ontology.db.models
and frozen here; from now on, schema changes are new revisions
(`alembic revision --autogenerate`). The audit-chain functions and triggers, the
row-level-security policies and the grants are hand-written: the ORM cannot express them.

Requires PostgreSQL 18 (uuidv7) and PostGIS. Run as a role that may create the schema;
the application role `s2_app` receives DML rights only.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _run(sql: str) -> None:
    # Raw driver execution with no parameter processing: this SQL contains `%I`
    # (format()) and `:MI` (to_char) which must reach the server untouched.
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(sql)


TABLES: list[str] = [
    """\
CREATE TABLE ontology.audit_log (
    id UUID DEFAULT uuidv7() NOT NULL,
    seq BIGINT NOT NULL,
    occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
    prev_hash BYTEA NOT NULL,
    entry_hash BYTEA NOT NULL,
    actor_type VARCHAR(32) NOT NULL,
    actor_id TEXT NOT NULL,
    on_behalf_of TEXT,
    event_type TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    outcome VARCHAR(32) NOT NULL,
    policy_decision_id TEXT,
    trace_id TEXT,
    details JSONB DEFAULT '{}'::jsonb NOT NULL,
    tenant_id UUID NOT NULL,
    CONSTRAINT pk_audit_log PRIMARY KEY (id),
    CONSTRAINT uq_audit_log_tenant_id_seq UNIQUE (tenant_id, seq),
    CONSTRAINT ck_audit_log_actor_type CHECK (actor_type IN ('user', 'service', 'agent')),
    CONSTRAINT ck_audit_log_outcome CHECK (outcome IN ('success', 'denied', 'error'))
)
""",
    """\
CREATE TABLE ontology.object_type (
    id UUID DEFAULT uuidv7() NOT NULL,
    api_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    description TEXT,
    status VARCHAR(32) DEFAULT 'draft' NOT NULL,
    version INTEGER DEFAULT 1 NOT NULL,
    backing_dataset TEXT,
    markings TEXT[] DEFAULT '{}'::text[] NOT NULL,
    ui JSONB DEFAULT '{}'::jsonb NOT NULL,
    tenant_id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    created_by TEXT NOT NULL,
    CONSTRAINT pk_object_type PRIMARY KEY (id),
    CONSTRAINT uq_object_type_tenant_id_api_name UNIQUE (tenant_id, api_name),
    CONSTRAINT ck_object_type_api_name_pascal CHECK (api_name ~ '^[A-Z][A-Za-z0-9]{0,62}$'),
    CONSTRAINT ck_object_type_status CHECK (status IN ('draft', 'active', 'deprecated'))
)
""",
    """\
CREATE TABLE ontology.action_type (
    id UUID DEFAULT uuidv7() NOT NULL,
    api_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    description TEXT,
    target_object_type_id UUID,
    status VARCHAR(32) DEFAULT 'draft' NOT NULL,
    version INTEGER DEFAULT 1 NOT NULL,
    parameters JSONB DEFAULT '{}'::jsonb NOT NULL,
    edits JSONB DEFAULT '[]'::jsonb NOT NULL,
    writeback JSONB DEFAULT '[]'::jsonb NOT NULL,
    policy_path TEXT DEFAULT 's2/actions/standard' NOT NULL,
    requires_approval BOOLEAN DEFAULT false NOT NULL,
    agent_access VARCHAR(32) DEFAULT 'none' NOT NULL,
    tenant_id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    created_by TEXT NOT NULL,
    CONSTRAINT pk_action_type PRIMARY KEY (id),
    CONSTRAINT uq_action_type_tenant_id_api_name UNIQUE (tenant_id, api_name),
    CONSTRAINT ck_action_type_api_name_camel CHECK (api_name ~ '^[a-z][A-Za-z0-9]{0,62}$'),
    CONSTRAINT fk_action_type_target_object_type_id_object_type FOREIGN KEY(target_object_type_id) REFERENCES ontology.object_type (id) ON DELETE RESTRICT,
    CONSTRAINT ck_action_type_status CHECK (status IN ('draft', 'active', 'deprecated')),
    CONSTRAINT ck_action_type_agent_access CHECK (agent_access IN ('none', 'propose'))
)
""",
    """\
CREATE TABLE ontology.link_type (
    id UUID DEFAULT uuidv7() NOT NULL,
    api_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    reverse_api_name TEXT NOT NULL,
    reverse_display_name TEXT NOT NULL,
    description TEXT,
    source_object_type_id UUID NOT NULL,
    target_object_type_id UUID NOT NULL,
    cardinality VARCHAR(32) NOT NULL,
    status VARCHAR(32) DEFAULT 'draft' NOT NULL,
    tenant_id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    created_by TEXT NOT NULL,
    CONSTRAINT pk_link_type PRIMARY KEY (id),
    CONSTRAINT uq_link_type_source_object_type_id_api_name UNIQUE (source_object_type_id, api_name),
    CONSTRAINT uq_link_type_target_object_type_id_reverse_api_name UNIQUE (target_object_type_id, reverse_api_name),
    CONSTRAINT ck_link_type_api_names_camel CHECK (api_name ~ '^[a-z][A-Za-z0-9]{0,62}$' AND reverse_api_name ~ '^[a-z][A-Za-z0-9]{0,62}$'),
    CONSTRAINT fk_link_type_source_object_type_id_object_type FOREIGN KEY(source_object_type_id) REFERENCES ontology.object_type (id) ON DELETE RESTRICT,
    CONSTRAINT fk_link_type_target_object_type_id_object_type FOREIGN KEY(target_object_type_id) REFERENCES ontology.object_type (id) ON DELETE RESTRICT,
    CONSTRAINT ck_link_type_cardinality CHECK (cardinality IN ('one_to_one', 'one_to_many', 'many_to_one', 'many_to_many')),
    CONSTRAINT ck_link_type_status CHECK (status IN ('draft', 'active', 'deprecated'))
)
""",
    """\
CREATE TABLE ontology.object_instance (
    id UUID DEFAULT uuidv7() NOT NULL,
    object_type_id UUID NOT NULL,
    primary_key TEXT NOT NULL,
    properties JSONB DEFAULT '{}'::jsonb NOT NULL,
    geometry geometry(GEOMETRY,4326),
    version INTEGER DEFAULT 1 NOT NULL,
    tenant_id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    created_by TEXT NOT NULL,
    CONSTRAINT pk_object_instance PRIMARY KEY (id),
    CONSTRAINT uq_object_instance_object_type_id_primary_key UNIQUE (object_type_id, primary_key),
    CONSTRAINT fk_object_instance_object_type_id_object_type FOREIGN KEY(object_type_id) REFERENCES ontology.object_type (id) ON DELETE RESTRICT
)
""",
    """\
CREATE TABLE ontology.property_type (
    id UUID DEFAULT uuidv7() NOT NULL,
    object_type_id UUID NOT NULL,
    api_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    description TEXT,
    data_type VARCHAR(32) NOT NULL,
    is_array BOOLEAN DEFAULT false NOT NULL,
    is_primary_key BOOLEAN DEFAULT false NOT NULL,
    is_title BOOLEAN DEFAULT false NOT NULL,
    is_required BOOLEAN DEFAULT false NOT NULL,
    unit TEXT,
    source_column TEXT,
    binding JSONB,
    constraints JSONB DEFAULT '{}'::jsonb NOT NULL,
    markings TEXT[] DEFAULT '{}'::text[] NOT NULL,
    status VARCHAR(32) DEFAULT 'active' NOT NULL,
    tenant_id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    created_by TEXT NOT NULL,
    CONSTRAINT pk_property_type PRIMARY KEY (id),
    CONSTRAINT uq_property_type_object_type_id_api_name UNIQUE (object_type_id, api_name),
    CONSTRAINT ck_property_type_api_name_camel CHECK (api_name ~ '^[a-z][A-Za-z0-9]{0,62}$'),
    CONSTRAINT ck_property_type_timeseries_bound CHECK (data_type <> 'timeseries' OR binding IS NOT NULL),
    CONSTRAINT ck_property_type_primary_key_scalar CHECK (NOT is_primary_key OR (data_type IN ('string', 'integer') AND NOT is_array AND is_required)),
    CONSTRAINT fk_property_type_object_type_id_object_type FOREIGN KEY(object_type_id) REFERENCES ontology.object_type (id) ON DELETE CASCADE,
    CONSTRAINT ck_property_type_data_type CHECK (data_type IN ('string', 'integer', 'double', 'decimal', 'boolean', 'date', 'timestamp', 'json', 'geopoint', 'geoshape', 'timeseries', 'media_ref')),
    CONSTRAINT ck_property_type_status CHECK (status IN ('draft', 'active', 'deprecated'))
)
""",
    """\
CREATE TABLE ontology.action_execution (
    id UUID DEFAULT uuidv7() NOT NULL,
    action_type_id UUID NOT NULL,
    action_type_version INTEGER NOT NULL,
    status VARCHAR(32) NOT NULL,
    parameters JSONB DEFAULT '{}'::jsonb NOT NULL,
    actor_type VARCHAR(32) NOT NULL,
    actor_id TEXT NOT NULL,
    on_behalf_of TEXT,
    approved_by TEXT,
    policy_decision_id TEXT,
    idempotency_key TEXT NOT NULL,
    error JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    completed_at TIMESTAMP WITH TIME ZONE,
    tenant_id UUID NOT NULL,
    CONSTRAINT pk_action_execution PRIMARY KEY (id),
    CONSTRAINT uq_action_execution_tenant_id_idempotency_key UNIQUE (tenant_id, idempotency_key),
    CONSTRAINT ck_action_execution_agent_has_principal CHECK (actor_type <> 'agent' OR on_behalf_of IS NOT NULL),
    CONSTRAINT fk_action_execution_action_type_id_action_type FOREIGN KEY(action_type_id) REFERENCES ontology.action_type (id) ON DELETE RESTRICT,
    CONSTRAINT ck_action_execution_status CHECK (status IN ('pending_approval', 'rejected', 'succeeded', 'failed')),
    CONSTRAINT ck_action_execution_actor_type CHECK (actor_type IN ('user', 'service', 'agent'))
)
""",
    """\
CREATE TABLE ontology.link_instance (
    id UUID DEFAULT uuidv7() NOT NULL,
    link_type_id UUID NOT NULL,
    source_object_id UUID NOT NULL,
    target_object_id UUID NOT NULL,
    properties JSONB DEFAULT '{}'::jsonb NOT NULL,
    tenant_id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    created_by TEXT NOT NULL,
    CONSTRAINT pk_link_instance PRIMARY KEY (id),
    CONSTRAINT uq_link_instance_link_type_id_source_object_id_target_object_id UNIQUE (link_type_id, source_object_id, target_object_id),
    CONSTRAINT fk_link_instance_link_type_id_link_type FOREIGN KEY(link_type_id) REFERENCES ontology.link_type (id) ON DELETE RESTRICT,
    CONSTRAINT fk_link_instance_source_object_id_object_instance FOREIGN KEY(source_object_id) REFERENCES ontology.object_instance (id) ON DELETE CASCADE,
    CONSTRAINT fk_link_instance_target_object_id_object_instance FOREIGN KEY(target_object_id) REFERENCES ontology.object_instance (id) ON DELETE CASCADE
)
""",
    """\
CREATE TABLE ontology.writeback_outbox (
    id UUID DEFAULT uuidv7() NOT NULL,
    action_execution_id UUID NOT NULL,
    connector TEXT NOT NULL,
    operation TEXT NOT NULL,
    payload JSONB DEFAULT '{}'::jsonb NOT NULL,
    status VARCHAR(32) DEFAULT 'pending' NOT NULL,
    attempts INTEGER DEFAULT 0 NOT NULL,
    next_attempt_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    delivered_at TIMESTAMP WITH TIME ZONE,
    last_error TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    tenant_id UUID NOT NULL,
    CONSTRAINT pk_writeback_outbox PRIMARY KEY (id),
    CONSTRAINT fk_writeback_outbox_action_execution_id_action_execution FOREIGN KEY(action_execution_id) REFERENCES ontology.action_execution (id) ON DELETE CASCADE,
    CONSTRAINT ck_writeback_outbox_status CHECK (status IN ('pending', 'delivered', 'dead'))
)
""",
]

INDEXES: list[str] = [
    """\
CREATE INDEX ix_audit_log_event ON ontology.audit_log (tenant_id, event_type, occurred_at)
""",
    """\
CREATE INDEX ix_audit_log_resource ON ontology.audit_log (tenant_id, resource_type, resource_id)
""",
    """\
CREATE INDEX ix_object_instance_geometry ON ontology.object_instance USING gist (geometry)
""",
    """\
CREATE INDEX ix_object_instance_properties ON ontology.object_instance USING gin (properties jsonb_path_ops)
""",
    """\
CREATE INDEX ix_object_instance_tenant_type ON ontology.object_instance (tenant_id, object_type_id)
""",
    """\
CREATE UNIQUE INDEX uq_property_type_one_pk ON ontology.property_type (object_type_id) WHERE is_primary_key
""",
    """\
CREATE UNIQUE INDEX uq_property_type_one_title ON ontology.property_type (object_type_id) WHERE is_title
""",
    """\
CREATE INDEX ix_action_execution_type_created ON ontology.action_execution (action_type_id, created_at)
""",
    """\
CREATE INDEX ix_link_instance_reverse ON ontology.link_instance (target_object_id, link_type_id)
""",
    """\
CREATE INDEX ix_writeback_outbox_due ON ontology.writeback_outbox (next_attempt_at) WHERE status = 'pending'
""",
]

# One definition of "what gets hashed", shared by the writer and the verifier so the
# two cannot drift. Timestamps are rendered in UTC explicitly: the default
# timestamptz -> json rendering depends on the session TimeZone and would make the hash
# unverifiable from a differently-configured session.
AUDIT_ENTRY_HASH = """\
CREATE FUNCTION ontology.audit_entry_hash(r ontology.audit_log) RETURNS bytea
LANGUAGE sql STABLE AS $$
    SELECT sha256(
        r.prev_hash || convert_to(
            jsonb_build_object(
                'id', r.id,
                'tenant_id', r.tenant_id,
                'seq', r.seq,
                'occurred_at', to_char(r.occurred_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                'actor_type', r.actor_type,
                'actor_id', r.actor_id,
                'on_behalf_of', r.on_behalf_of,
                'event_type', r.event_type,
                'resource_type', r.resource_type,
                'resource_id', r.resource_id,
                'outcome', r.outcome,
                'policy_decision_id', r.policy_decision_id,
                'trace_id', r.trace_id,
                'details', r.details
            )::text,
            'UTF8'
        )
    );
$$
"""

# Appends are serialised per tenant with an advisory lock. Under READ COMMITTED the
# SELECT then sees the previous writer's row. Under REPEATABLE READ it may not, in which
# case uq_audit_log_tenant_id_seq rejects the insert and the caller retries: the chain
# can fail closed but cannot fork. Client-supplied values for seq, prev_hash,
# occurred_at and entry_hash are always overwritten.
AUDIT_CHAIN = """\
CREATE FUNCTION ontology.audit_chain() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    last_seq  bigint;
    last_hash bytea;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('s2.audit:' || NEW.tenant_id::text, 0));

    SELECT seq, entry_hash INTO last_seq, last_hash
      FROM ontology.audit_log
     WHERE tenant_id = NEW.tenant_id
     ORDER BY seq DESC
     LIMIT 1;

    NEW.seq         := COALESCE(last_seq, 0) + 1;
    NEW.prev_hash   := COALESCE(last_hash, decode(repeat('00', 32), 'hex'));
    NEW.occurred_at := clock_timestamp();
    NEW.entry_hash  := ontology.audit_entry_hash(NEW);
    RETURN NEW;
END;
$$
"""

AUDIT_IMMUTABLE = """\
CREATE FUNCTION ontology.audit_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'ontology.audit_log is append-only: % rejected', TG_OP
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$
"""

AUDIT_TRIGGERS = """\
CREATE TRIGGER audit_log_chain BEFORE INSERT ON ontology.audit_log
    FOR EACH ROW EXECUTE FUNCTION ontology.audit_chain()

CREATE TRIGGER audit_log_no_rewrite BEFORE UPDATE OR DELETE ON ontology.audit_log
    FOR EACH ROW EXECUTE FUNCTION ontology.audit_immutable()

CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON ontology.audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION ontology.audit_immutable()
"""

# Re-walks a tenant's chain. first_bad_seq is NULL when every entry checks out.
# Detects edits, reordering and gaps. It cannot detect removal of the newest entries on
# its own: that needs the head hash anchored outside this database (an object-lock
# bucket or a transparency log), which is Phase 4 hardening.
AUDIT_VERIFY = """\
CREATE FUNCTION ontology.audit_verify(p_tenant uuid)
RETURNS TABLE (entries bigint, first_bad_seq bigint)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    r             ontology.audit_log;
    expected_prev bytea  := decode(repeat('00', 32), 'hex');
    expected_seq  bigint := 1;
BEGIN
    entries := 0;
    first_bad_seq := NULL;
    FOR r IN SELECT * FROM ontology.audit_log WHERE tenant_id = p_tenant ORDER BY seq LOOP
        entries := entries + 1;
        IF r.seq <> expected_seq
           OR r.prev_hash <> expected_prev
           OR r.entry_hash <> ontology.audit_entry_hash(r) THEN
            first_bad_seq := r.seq;
            EXIT;
        END IF;
        expected_prev := r.entry_hash;
        expected_seq  := r.seq + 1;
    END LOOP;
    RETURN NEXT;
END;
$$
"""

# Tenant isolation. The engine sets `s2.tenant_id` from the caller's token,
# transaction-locally, at the start of every transaction. With the setting absent or
# empty the predicate is NULL and no row is visible or writable: fail-closed.
# FORCE makes the policies apply to the table owner too; only superusers and roles
# with BYPASSRLS are exempt.
TENANT_ISOLATION = """\
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'object_type', 'property_type', 'link_type', 'action_type',
        'object_instance', 'link_instance', 'action_execution',
        'writeback_outbox', 'audit_log'
    ] LOOP
        EXECUTE format('ALTER TABLE ontology.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE ontology.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON ontology.%I '
            'USING      (tenant_id = NULLIF(current_setting(''s2.tenant_id'', true), '''')::uuid) '
            'WITH CHECK (tenant_id = NULLIF(current_setting(''s2.tenant_id'', true), '''')::uuid)',
            t
        );
    END LOOP;
END;
$$
"""

# The application role. Local Compose creates it with a password before migrating;
# elsewhere it is created here without login and the operator grants access.
# It gets DML only, and on the audit log only SELECT and INSERT.
GRANTS = """\
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 's2_app') THEN
        CREATE ROLE s2_app NOLOGIN;
    END IF;
END;
$$

GRANT USAGE ON SCHEMA ontology TO s2_app

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA ontology TO s2_app

REVOKE UPDATE, DELETE, TRUNCATE ON ontology.audit_log FROM s2_app

GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA ontology TO s2_app

ALTER DEFAULT PRIVILEGES IN SCHEMA ontology GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO s2_app
"""


def upgrade() -> None:
    _run("CREATE EXTENSION IF NOT EXISTS postgis")
    _run("CREATE SCHEMA ontology")
    for statement in (*TABLES, *INDEXES):
        _run(statement)
    for statement in (AUDIT_ENTRY_HASH, AUDIT_CHAIN, AUDIT_IMMUTABLE, AUDIT_VERIFY):
        _run(statement)
    for statement in AUDIT_TRIGGERS.strip().split("\n\n"):
        _run(statement)
    _run(TENANT_ISOLATION)
    for statement in GRANTS.strip().split("\n\n"):
        _run(statement)


def downgrade() -> None:
    # Drops every object and all data in the schema. The role s2_app is left in place.
    _run("DROP SCHEMA ontology CASCADE")
