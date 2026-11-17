# Action executor

Delivers the write-backs that governed actions queue.

When an action executes, `services/ontology-engine` inserts one row per write-back into `ontology.writeback_outbox`, in the same transaction as the ontology change. This service picks those rows up and sends them to the external system named by the row's `connector`.

```
outbox (pending) --claim--> connector.deliver() --> delivered  + audit entry
                                  |
                                  +-- transient error --> pending again, later (backoff)
                                  +-- permanent error, or retries used up --> dead + audit entry + ERROR log
```

Design and trade-offs: [ADR 0009](../../docs/architecture/adr/0009-action-executor.md).

## Run

Part of the Compose stack (`make up`). By default every connector is the `log` type: it records the write-back in the service log and reports success, so the loop is visible with no external system.

```bash
make seed
TOKEN=$(make -s token AS=operator)
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"parameters": {"droneId": "DRN-0003", "reason": "demo"}}' \
  http://localhost:8000/v1/actions/groundDrone/apply     # the response lists the write-back as "pending"
make outbox                                              # a second later: "delivered"
docker logs s2-action-executor                           # "write-back recorded, not sent"
```

A stack created before Phase 2 has no login for the executor's role: run `make executor-role` once, after `make up` (it needs PostgreSQL running).

Outside Compose: `python -m s2_executor` with the environment below.

## Configuration

All variables are prefixed `S2_`.

| Variable | Default | Meaning |
|---|---|---|
| `EXECUTOR_DATABASE_URL` | `postgresql+psycopg://s2_executor:s2_executor_dev@localhost:5432/s2` | The `s2_executor` role. Never a superuser, never `s2_app`: the service checks. |
| `CONNECTORS` | `samanvaya-fleet` and `cmms`, both `log` | JSON object, see below |
| `POLL_INTERVAL_S` | `1` | Sleep when a pass finds fewer than `BATCH_SIZE` rows |
| `BATCH_SIZE` | `10` | Rows claimed per pass |
| `LEASE_S` | `60` | How long a claimed row is hidden from other workers. Keep it above the slowest connector timeout |
| `MAX_ATTEMPTS` | `8` | Attempts, including ones that crashed, before a row goes dead |
| `BACKOFF_BASE_S`, `BACKOFF_CAP_S` | `5`, `900` | Retry delay: base doubling per attempt up to the cap, with 50-100% jitter |

### Connectors

```bash
S2_CONNECTORS='{
  "cmms":            {"type": "http", "base_url": "https://cmms.example.com/api", "bearer_token": "...",
                      "timeout_s": 10, "paths": {"create_work_order": "/v2/work-orders"}},
  "samanvaya-fleet": {"type": "log"}
}'
```

Keys match the `connector` field in ontology packs. An `http` connector POSTs the payload as JSON to `base_url` + the operation's path (default `/<operation>`) with these headers:

- `Idempotency-Key`: the outbox row id. **The target must treat a repeat as the same request**: delivery is at-least-once.
- `X-S2-Operation`: the operation name.

| Response | Treated as |
|---|---|
| 2xx | delivered |
| 5xx, 408, 425, 429, timeout, connection error | transient: retry with backoff |
| other 3xx and 4xx | permanent: dead at once |

A row naming a connector that is not configured goes dead at once.

Adding a type (SAP PM, Maximo) means a class with `deliver(operation, payload, idempotency_key)` and `close()` in `connectors.py` that raises `TransientError` or `PermanentError`, plus an entry in `build_connectors`.

## Operating it

- **Dead rows need a person.** Each logs at ERROR with `alert: true` and writes a `writeback.dead` audit entry. List them with `make outbox`, or `SELECT * FROM ontology.writeback_outbox WHERE status = 'dead'`. There is no redrive command yet: after fixing the cause, the schema owner can set `status = 'pending', attempts = 0, next_attempt_at = now()` on the rows.
- **Several executors can run at once.** A row is claimed by one worker at a time.
- **Payloads are not logged or audited**, only their key names (log connector) and the connector, operation, attempt count and error text.

## Database role

`s2_executor`, created by migration `0002` (and with a password by Compose's init script). It has `BYPASSRLS`, so it sees every tenant's outbox rows, and almost nothing else:

| On | Rights |
|---|---|
| `writeback_outbox` | `SELECT`; `UPDATE` of `status`, `attempts`, `next_attempt_at`, `delivered_at`, `last_error` only |
| `audit_log` | `SELECT`, `INSERT` (the chain trigger fills in sequence and hashes; there is no `UPDATE` or `DELETE`) |
| Everything else | none |

## Tests

```bash
make test                                           # connector tests; relay tests skip without a database
S2_TEST_ADMIN_URL=postgresql+psycopg://postgres:postgres_dev@localhost:5432/postgres make test
```

The relay tests run against a real PostgreSQL migrated with the ontology engine's own revisions. They cover claiming, retry and backoff, dead letters, tenant audit chains, two workers racing, a crashed worker, a late finish after lease expiry, and what the role can and cannot do.
