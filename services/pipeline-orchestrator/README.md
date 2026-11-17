# Pipeline orchestrator (lakehouse)

Versioned datasets in Apache Iceberg, their sync into ontology objects, dataset branches, and Dagster to schedule it and show the lineage. Design and trade-offs: [ADR 0011](../../docs/architecture/adr/0011-lakehouse-catalog-and-sync.md).

```
sources (ERP, CMMS, CLI) --> Iceberg table assets.commercial_drone   (catalog: Postgres schema `lakehouse`,
                                  |   main, branches                   files: s3://s2-lakehouse)
                                  v
                    sync (plan -> PATCH / PUT through the engine API, with provenance)
                                  v
                    CommercialDrone objects, each change audited with the snapshot it came from
```

An object type names the dataset that backs it (`backing_dataset`) and each property it takes from that dataset names its column (`source_column`). The sync owns only those properties. `core-fleet`'s `CommercialDrone` maps `drone_id`, `call_sign`, `model`, `serial_number` and `firmware_version`; its status, position and grounding stay with operations and actions.

## Try it on the local stack

```bash
make seed                 # the packs (core-fleet 0.2.0 has the column mapping) and the demo drones
make lakehouse-demo       # write the demo asset register to Iceberg, then sync: serial numbers and firmware appear
```

Then a change on a branch, previewed, promoted and synced:

```bash
make lakehouse ARGS="branch create assets.commercial_drone preview"
make lakehouse ARGS="demo-rollout --branch preview"                      # firmware 4.2.0 on DRN-0003, on the branch only
make lakehouse ARGS="sync CommercialDrone --branch preview --dry-run"    # what promoting would change: one property, one drone
make lakehouse ARGS="branch promote assets.commercial_drone preview"     # main fast-forwards to the branch
make lakehouse ARGS="sync CommercialDrone"                               # the ontology catches up
```

The audit log shows where each value came from: `object.modified` entries carry `provenance` with the dataset, branch, snapshot id and run id.

`make pipelines-ui` starts Dagster at http://localhost:3000: the asset graph (dataset → object type), each run's snapshot, column mapping and counts, and the `ontology_sync` schedule (off until you turn it on).

## CLI

`python -m s2_pipelines` (in the stack: `make lakehouse ARGS="..."`):

| Command | Does |
|---|---|
| `demo-load` | write the demo asset register to `main` |
| `demo-rollout --branch B [--version V]` | write a firmware upgrade to branch `B` |
| `branch list\|create\|promote\|drop DATASET [NAME]` | manage branches; promote is fast-forward only |
| `sync TYPE [--branch B] [--dry-run] [--allow-branch]` | make `TYPE` agree with its dataset. Exits 1 if any row was rejected |

## What a sync does

| Row | Result |
|---|---|
| key has no object | created, if the dataset supplies every required property; otherwise rejected with the reason |
| mapped values differ | `PATCH` of just those values |
| mapped values match | nothing (not even an audit entry) |
| key empty or repeated | rejected |
| object with no row | reported as `absent`, left alone |

Writes go through the engine API as the pipeline's identity, so role checks, markings and validation apply; a refused row is reported, not fatal.

## Configuration

All variables are prefixed `S2_`.

| Variable | Default | Meaning |
|---|---|---|
| `CATALOG_URI` | `postgresql+psycopg://s2_app:...@localhost:5432/s2?options=-csearch_path%3Dlakehouse` | The SQL catalog. Any SQLAlchemy URL works (tests use SQLite) |
| `WAREHOUSE` | `s3://s2-lakehouse` | Where files go: `s3://bucket[/prefix]` or `file:///path` |
| `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_REGION` | SeaweedFS dev values | For an `s3://` warehouse |
| `ENGINE_API_URL` | `http://localhost:8000` | The ontology engine |
| `PIPELINE_CLIENT_ID`, `PIPELINE_CLIENT_SECRET` | unset | A service account (client credentials). Set both in production |
| `PIPELINE_USER`, `PIPELINE_PASSWORD` | `admin`, `admin-dev` | Used when no client secret is set: the dev password grant |
| `SYNC_TARGETS` | `{"CommercialDrone": "assets.commercial_drone"}` | What Dagster syncs. Must match the packs; a mismatch fails the run |
| `SYNC_CRON` | `*/10 * * * *` | The schedule, when turned on |

## Tests

```bash
make test        # catalog, branches, planning, CLI, Dagster: no server needed
S2_TEST_ADMIN_URL=postgresql+psycopg://postgres:postgres_dev@localhost:5432/postgres make test   # + end to end
S2_TEST_S3_ENDPOINT=http://localhost:8333 make test    # + the lakehouse on the stack's SeaweedFS
```

The end-to-end tests run the real engine in-process on a migrated PostgreSQL and call it over its HTTP API, exactly as the sync does in production.

## Not yet

Deleting objects whose rows disappeared; incremental sync from snapshot diffs; dbt transformations; branching several datasets at once; per-tenant datasets; table maintenance (snapshot expiry, compaction). ADR 0011 says why each is deferred.
