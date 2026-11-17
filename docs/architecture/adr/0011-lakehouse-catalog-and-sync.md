# 0011. The lakehouse: PyIceberg's SQL catalog, per-table branches, and a sync through the API

Status: accepted (November 2026). Settles the open catalog decision listed since Phase 1.

## Context

The specification asks for versioned datasets, dataset branching ("test schema or model changes safely before merging to production"), lineage, and object types materialised from datasets. The packs already name a dataset per object type (`backing_dataset`) and a column per property (`source_column`), but nothing read them.

The catalog was the decision to make first, because it decides what a branch is:

| Option | Branches | Cost |
|---|---|---|
| PyIceberg's SQL catalog in the existing PostgreSQL | per table (Iceberg refs) | none: two tables in a schema |
| A REST catalog (Lakekeeper, Polaris) | per table | one more service; other engines (Spark, Trino, DuckDB) can share the tables |
| Nessie | across many tables at once, like git | a JVM service, on machines that already struggle with Kafka and Keycloak |

## Decision

**SQL catalog, in PostgreSQL schema `lakehouse` of the ontology database** (migration 0003), with data and metadata files on the object store (`s3://s2-lakehouse`). It adds no service. The pipeline code reaches the catalog only through PyIceberg's `Catalog` interface, so moving to a REST catalog later is a configuration change plus a one-time registration of existing tables, not a rewrite.

**Branches are Iceberg's own, per table.** A branch starts at `main`'s current snapshot and takes writes of its own. Promotion fast-forwards `main` to the branch and is refused if `main` has moved since the branch was cut, because moving `main` would silently drop those writes. There is no merge. Branching several datasets together, atomically, needs Nessie or a catalog with multi-table transactions, and is not offered.

**The sync goes through the engine's public API**, as `services/pipeline-orchestrator/README.md` intended, not into the database. It is therefore governed like any client: the role check, markings (a property the pipeline identity is not cleared for is refused, and the row is reported), validation and audit all apply. Two engine additions make that workable:

- `PATCH /v1/objects/{type}/{key}` changes only the properties named, so the sync never touches what it does not own (status, position, anything an action set). A patch that changes nothing is not audited, so a repeated sync leaves no trace.
- Writes accept `provenance` (`dataset`, `snapshot_id`, `branch`, `run_id`), recorded in the audit entry. That is the record-level lineage: every value the sync wrote names the dataset snapshot it came from.
- `PUT` with `If-None-Match: *` creates only (409 if the object exists), so a sync's create can never replace an object made between its plan and its write.

**Sync semantics.** One snapshot is read; for each row the mapped properties are made to agree. Rows for unknown keys create objects only if the dataset supplies every required property; otherwise the row is rejected with the reason. Objects with no row are reported as `absent` and left alone. A dry run (`plan`) writes nothing; pointed at a branch it previews what promoting the branch would do to the ontology. Writing the ontology straight from a branch needs an explicit `--allow-branch`.

**Dagster orchestrates and shows lineage.** Each dataset is an external asset and each synced object type an asset depending on it, so the asset graph is the dataset-level lineage. Each materialisation records the snapshot read, the column-to-property mapping applied (column-level lineage) and the counts. The schedule ships stopped.

**dbt is not used yet.** There are no SQL transformations between sources and the datasets that back object types; dbt comes with the first one.

## Consequences

- The catalog is deployment-wide. Datasets are not tenant-scoped: the sync writes into the tenant of its token. Per-tenant datasets would mean a namespace per tenant and a pipeline identity per tenant.
- The pipeline connects to the catalog as `s2_app` in development. In production it should have a role of its own with rights on schema `lakehouse` only.
- The ontology lags the dataset by the sync interval, and a run is not atomic across objects: each row is its own API call and its own audit entry. A failed row does not stop the rest.
- Deleting objects whose rows disappeared was left out here; [ADR 0012](0012-cmms-adapters-and-sync-identity.md) adds it, opt-in and bounded.
- Each sync lists every object of the type and compares in memory. Fine for asset registers of thousands; an incremental sync from Iceberg's snapshot diffs is the path to large tables.
- SeaweedFS works for Iceberg commits, branches included, once given enough volumes: its single-server defaults fit about one bucket, so the Compose stack now runs it with `-volume.max=100 -master.volumeSizeLimitMB=256` (ADR 0002).
- Tags, time travel by timestamp and table maintenance (expiring snapshots, compaction) are available in Iceberg and not yet exposed.
