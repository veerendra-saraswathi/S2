# Architecture decision records

One file per decision: the context, what was decided, and what it costs. A decision that is replaced gets a new record that says so; the old one stays.

| # | Decision | Status |
|---|---|---|
| [0001](0001-monorepo-and-tooling.md) | One repository; uv, pnpm and ruff | Accepted |
| [0002](0002-object-store.md) | SeaweedFS instead of MinIO | Accepted |
| [0003](0003-mqtt-broker.md) | Mosquitto for development; production broker undecided | Accepted, part open |
| [0004](0004-ontology-storage.md) | PostgreSQL 18; instances in JSONB with one geometry column | Accepted |
| [0005](0005-actions-and-outbox.md) | Actions commit in one transaction; write-backs through an outbox | Accepted |
| [0006](0006-audit-chain.md) | Per-tenant hash chain in PostgreSQL | Accepted |
| [0007](0007-tenancy.md) | Tenant isolation by row-level security | Accepted |
| [0008](0008-telemetry-path.md) | Telemetry keyed by natural key, through Kafka | Accepted |

Open decisions, to be recorded when made: the Iceberg catalog (it determines how dataset branching works), whether a graph engine is needed beyond PostgreSQL, and the production MQTT broker.
