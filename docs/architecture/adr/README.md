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
| [0009](0009-action-executor.md) | Action executor: leased claims, one-transaction outcomes, direct audit append, own DB role | Accepted |
| [0010](0010-markings-on-reads.md) | Markings enforced on reads in the engine: all-of, from `marking:` realm roles | Accepted |
| [0011](0011-lakehouse-catalog-and-sync.md) | Lakehouse: PyIceberg SQL catalog, per-table branches, sync through the API | Accepted |
| [0012](0012-cmms-adapters-and-sync-identity.md) | Maximo and SAP PM adapters look up before create; bounded absent deletes; sync as a service | Accepted |
| [0013](0013-workshop-ui.md) | Workshop UI: thin client of the public API, PKCE sign-in, MapLibre map | Accepted |
| [0014](0014-triage-rules.md) | Triage rules in packs, evaluated on read, with evidence and markings | Accepted |
| [0015](0015-governed-agents.md) | Governed agents: small loop on local Ollama, rationale required, four eyes through the agent | Accepted |

Open decisions, to be recorded when made: whether a graph engine is needed beyond PostgreSQL, and the production MQTT broker.
