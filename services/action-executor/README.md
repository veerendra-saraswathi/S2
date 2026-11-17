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

### Maximo and SAP PM

Two adapters turn the `create_work_order` write-back (`external_id`, `title`, `priority`, `description`) into a record in the maintenance system ([ADR 0012](../../docs/architecture/adr/0012-cmms-adapters-and-sync-identity.md)). Point the pack's `cmms` connector at one of them:

```bash
# IBM Maximo Manage: a work order through the JSON API
S2_CONNECTORS='{"cmms": {"type": "maximo", "base_url": "https://maximo.example.com",
                         "api_key": "...", "site_id": "BEDFORD", "work_type": "CM"},
                "samanvaya-fleet": {"type": "log"}}'

# SAP S/4HANA PM: a maintenance notification through API_MAINTNOTIFICATION
S2_CONNECTORS='{"cmms": {"type": "sap-pm", "base_url": "https://s4.example.com", "sap_client": "100",
                         "username": "S2_COMM", "password": "...", "notification_type": "M1",
                         "reference_field": "YY1_S2WorkOrder_NOT"},
                "samanvaya-fleet": {"type": "log"}}'
```

| Setting | Maximo | SAP PM |
|---|---|---|
| required | `base_url`, `api_key`, `site_id` | `base_url`, and `username` + `password` or `bearer_token` |
| optional | `object_structure` (`mxapiwo`), `work_type`, `reference_field` (`externalrefid`), `priorities`, `timeout_s` | `service_path`, `sap_client`, `notification_type` (`M1`), `reference_field`, `priorities`, `timeout_s` |

- **Retries are safe** because each adapter looks up S2's work-order id before it creates anything, and does nothing if the record exists. Maximo stores the id in `externalrefid`. SAP has no standard free field for it: set `reference_field` to one (often a custom field), or a retry after a lost response can create a second notification.
- **Priorities** map `CRITICAL/HIGH/MEDIUM/LOW` to `1/2/3/4` unless `priorities` says otherwise. An unmapped priority, a missing title, an id that is not a plain identifier, or any operation other than `create_work_order` is a permanent error.
- **Text limits:** Maximo's description holds 100 characters (a longer title goes to the long description too); SAP's notification text holds 40.
- **SAP's CSRF token** is fetched with its session cookie and fetched again, once, if it expires.

These are tested against fakes written from IBM's and SAP's documentation, not yet against a real system. Expect the first connection to need configuration.

Adding another type means a class with `deliver(operation, payload, idempotency_key)` and `close()` that raises `TransientError` or `PermanentError` (`connectors.py` has `send` and `check` to classify HTTP failures), a config class in `settings.py`, and an entry in `build_connectors`.

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
