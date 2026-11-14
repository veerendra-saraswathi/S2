# 0008. Telemetry keyed by natural key, through Kafka

Status: accepted (November 2026). The contract itself is a placeholder.

## Context

Fleet telemetry arrives over MQTT at a high rate and must end up in a time-series store that the ontology can read as properties of objects. Vehicles appear on the bus before anyone registers them in the ontology, and stores get rebuilt.

## Decision

- **Path**: MQTT -> `mqtt-bridge` -> Kafka topic `s2.telemetry.raw` -> `timeseries-sink` -> TimescaleDB. Kafka is the durable log; every store is a consumer of it and can be rebuilt by replaying.
- **Key**: readings are stored under `(tenant, object type, primary key, metric, time)`. The ontology's internal object id is not used. Ingestion never calls the ontology, and a reading for an object that does not exist yet is simply stored.
- **Binding**: a `timeseries` property on an object type names a metric. Reading the property is a query on the telemetry store by the object's natural key.
- **Validation at the edge of S2**: the bridge validates each message against the contract and sends rejects, with the reason, to `s2.telemetry.dlq`.
- **Delivery**: Kafka messages are keyed by object, so one object's readings stay ordered. The sink commits offsets after the database commit and inserts with `ON CONFLICT DO NOTHING` on the natural key, which makes replays harmless.
- **Contract**: topic `s2/v1/{tenant}/{ObjectType}/{key}/telemetry` and the JSON payload defined in `services/ingestion-connectors/src/s2_ingest/contracts.py`.

## Consequences

- The contract was written without sight of the real Samanvaya 0.5 topics and payloads. Aligning the two is the first job on this path; it is confined to `contracts.py`.
- Renaming an object's primary key orphans its history. Primary keys are treated as immutable.
- The MQTT hop acknowledges on receipt, so a bridge crash can lose in-flight messages. Acceptable for periodic telemetry; not for events that must not be lost.
- The telemetry tables have no row-level security. The engine filters by tenant in its queries. A dedicated least-privilege role and RLS on these tables are Phase 2 work.
- The tenant is taken from the MQTT topic. That is only as trustworthy as the broker's access control, which the local stack does not have.
