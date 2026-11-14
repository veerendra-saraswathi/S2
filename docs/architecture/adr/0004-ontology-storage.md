# 0004. PostgreSQL 18; instances in JSONB with one geometry column

Status: accepted (November 2026)

## Context

The ontology has two kinds of data: definitions (object types, properties, links, actions) and instances (objects and links). Definitions change at run time, when a pack is applied. Instances must be typed, queryable, spatial and transactional with the audit log and the action outbox.

## Decision

- **One PostgreSQL 18 database** holds definitions, instances, action executions, the outbox and the audit log, so that an action can change all of them in one transaction ([0005](0005-actions-and-outbox.md)).
- **Instances are rows in two generic tables.** `object_instance` keeps scalar properties in a JSONB column and its shape in one PostGIS `geometry` column (SRID 4326); `link_instance` joins two objects. The engine validates every write against the property definitions; the database does not know the property types.
- **Time-series properties hold no values.** They bind to a metric in TimescaleDB ([0008](0008-telemetry-path.md)).
- **Primary keys are UUIDv7** from PostgreSQL 18's `uuidv7()`: time-ordered, so inserts stay local in the indexes.
- **Enumerations are `VARCHAR` with a `CHECK`**, not native enum types, so adding a value is an ordinary migration.
- **Links are traversed with joins.** No graph database.

## Consequences

- Applying a pack never runs DDL, so definitions can change while the system is live and, later, on a branch.
- Type safety of instance data rests on the engine. Anything that writes instances must go through it; nothing else is granted the right.
- Filtering by property uses the GIN index on the JSONB column. Properties that become hot filter or sort keys will want expression indexes, added by migration when measured.
- **One geometry per object.** An object type may declare at most one `geopoint` / `geoshape` property. Types that need several shapes (a footprint and a centroid, say) need either a second column or a geometry-collection convention; decide when a real case appears.
- PostgreSQL 18 is a hard requirement (for `uuidv7()`).
- Multi-hop graph queries beyond a few joins are not served well. If they become a requirement, Apache AGE (an extension, so still one database and one transaction) is the first thing to evaluate.
