# Ingestion connectors

Brings field telemetry into S2.

```
MQTT  --mqtt-bridge-->  Kafka (s2.telemetry.raw)  --timeseries-sink-->  TimescaleDB
                           \--> s2.telemetry.dlq   (messages that fail the contract)
```

| Process | Run | Does |
|---|---|---|
| `mqtt-bridge` | `python -m s2_ingest.mqtt_bridge` | Subscribes to `s2/v1/+/+/+/telemetry`, validates, forwards to Kafka keyed by object |
| `timeseries-sink` | `python -m s2_ingest.timeseries_sink` | Batches from Kafka into `telemetry.reading` and `telemetry.position`. Run as many as the topic has partitions to share: the local stack runs two, `timeseries-sink` and `timeseries-sink-2`, over 4 partitions |

## The contract

`src/s2_ingest/contracts.py` implements the topic layout and payload of [telemetry contract v1](../../contracts/telemetry/v1/README.md), and defines the Kafka envelope. v1 is frozen: `tests/test_contract_v1.py` holds the module to v1's JSON Schema and conformance cases, and a change to what is accepted is v2, not an edit here. v1 was written before the real Samanvaya 0.5 topics and payloads were available.

## Guarantees

- Per-object ordering on Kafka (messages are keyed by tenant, object type and primary key).
- Kafka to TimescaleDB is at-least-once and idempotent: offsets are committed after the database transaction, and inserts ignore rows that already exist.
- MQTT to Kafka acknowledges on receipt: a bridge crash can drop in-flight messages.
- Run one `mqtt-bridge` replica. It holds a persistent MQTT session under a fixed client id.
- The bridge logs in as `s2-bridge`, which the broker's ACL lets read telemetry and publish nothing. Its password comes from a file (`S2_MQTT_PASSWORD_FILE`, a Compose secret); `S2_MQTT_USERNAME` names the user, and an `mqtts://` `S2_MQTT_URL` verifies the broker against `S2_MQTT_CA_FILE`.

## Schema

`sql/001_telemetry.sql` creates the hypertables. In the local stack it is applied when the `timescaledb` container first initialises. It is not yet under a migration tool.

## Not here yet

Change data capture, batch connectors, and ERP / CMMS / SCADA sources (Phase 2).
