# Telemetry contract v1

**Frozen 2026-11-27.** This is what a field gateway publishes to S2's broker and what S2's
MQTT bridge accepts. It replaces the placeholder that `contracts.py` described until now.

| File | What it holds |
|---|---|
| [`message.schema.json`](message.schema.json) | The payload, as JSON Schema 2020-12 |
| [`topic.schema.json`](topic.schema.json) | The topic, as a pattern |
| [`cases.json`](cases.json) | Conformance cases: topics and payloads, each valid or not, with the reason |

S2's implementation is `services/ingestion-connectors/src/s2_ingest/contracts.py`, and
`tests/test_contract_v1.py` beside it holds it to these files. A producer in another
repository pins a copy of this directory and runs the same cases against its own output.

## Topic

```
s2/v1/{tenant_id}/{ObjectType}/{object_pk}/telemetry
```

- `tenant_id`: a UUID, lowercase and hyphenated. Broker ACLs match topics as strings, so
  `6F1C…` and `6f1c…` are different topics and only one of them is granted.
- `ObjectType`: PascalCase, `[A-Z][A-Za-z0-9]{0,62}`: an ontology object type.
- `object_pk`: the object's primary key, as one topic level: anything but `/`, `+` and `#`.
  S2 stores readings under it whether or not the object exists yet (ADR 0008).

## Payload

```json
{
  "ts": "2026-11-14T17:00:00.123Z",
  "metrics": {"battery_voltage": 22.4, "armed": true, "flight_mode": "AUTO"},
  "position": {"lat": 17.44, "lon": 78.35, "alt_m": 120.5, "heading_deg": 87.0, "speed_mps": 12.1},
  "seq": 1234
}
```

| Field | Required | Rule |
|---|---|---|
| `ts` | yes | When the reading was taken, at the source. RFC 3339 with an upper-case `T`, seconds, at most six fractional digits, and `Z` or an offset. Not epoch seconds |
| `metrics` | one of the two | Names `^[a-z][a-z0-9_]{0,62}$`; values a number, a boolean or a string. A boolean is stored as 1 or 0, a string as a label |
| `position` | one of the two | `lat` −90..90 and `lon` −180..180 required; `alt_m`, `heading_deg` 0..<360 and `speed_mps` ≥ 0 optional |
| `seq` | no | A whole number ≥ 0, counted by the source. Not used for ordering or de-duplication |

A message carries at least one metric or a position. Any field not listed is refused,
anywhere in the payload; a producer with more to say sends it as a metric. Optional fields
may be absent or `null`. Values are JSON values of the stated type: S2 converts nothing, so
`"17.4"` is not a latitude and `true` is not a sequence number.

Six fractional digits is the most S2 stores. A seventh is refused rather than rounded,
because two readings rounded to the same instant are one row: the sink inserts
`ON CONFLICT DO NOTHING` on the time and key.

A message that breaks a rule goes to `s2.telemetry.dlq` with the reason, and is not stored.

## Changing it

v1 does not change. Within it, producers may add **metrics**: they are data, not contract.

Anything else is v2, published under `s2/v2/…`: a new or renamed field, a looser or
tighter rule, a new payload shape. S2 accepts v1 and v2 side by side until every producer
has moved, and v1 is retired by a decision recorded in an ADR, not by an edit here.

A mistake in a case or a schema, where the two disagree with this page or with each other,
is fixed in place, and the commit says which one was wrong and why.

## Not decided by v1

- **Alignment with Samanvaya 0.5.** v1 was written before the fleet side's topics and
  payloads were available. If 0.5 does not adopt v1, the bridge maps 0.5's form onto v1, or
  0.5's form becomes v2.
- **Duplicate keys** in one JSON object. Parsers differ; S2's keeps the last. Producers must
  not send them.
- **Size limits** on a message, a metric's string value, or the number of metrics.
