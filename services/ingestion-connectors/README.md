# Ingestion connectors

Brings field telemetry into S2.

```
MQTT  --mqtt-bridge-->  Kafka (s2.telemetry.raw)  --timeseries-sink-->  TimescaleDB
                           \--> s2.telemetry.dlq   (messages that fail the contract)
```

| Process | Run | Does |
|---|---|---|
| `mqtt-bridge` | `python -m s2_ingest.mqtt_bridge` | Subscribes to `s2/v1/+/+/+/telemetry`, validates, forwards to Kafka keyed by object |
| `timeseries-sink` | `python -m s2_ingest.timeseries_sink` | Batches from Kafka into `telemetry.reading` and `telemetry.position` |

## The contract

`src/s2_ingest/contracts.py` is the single definition of the topic layout, the payload and the Kafka envelope. **It is a placeholder**: S2's own design, written before the real Samanvaya 0.5 topics and payloads were available. Aligning with the fleet side means editing that module and its tests; nothing downstream needs to change.

## Guarantees

- Per-object ordering on Kafka (messages are keyed by tenant, object type and primary key).
- Kafka to TimescaleDB is at-least-once and idempotent: offsets are committed after the database transaction, and inserts ignore rows that already exist.
- MQTT to Kafka acknowledges on receipt: a bridge crash can drop in-flight messages.
- Run one `mqtt-bridge` replica. It holds a persistent MQTT session under a fixed client id.

## Schema

`sql/001_telemetry.sql` creates the hypertables. In the local stack it is applied when the `timescaledb` container first initialises. It is not yet under a migration tool.

## Not here yet

Change data capture, batch connectors, and ERP / CMMS / SCADA sources (Phase 2).
