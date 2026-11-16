# 0009. The action executor claims with a lease, finalizes in one transaction, and appends to the audit chain itself

Status: accepted (November 2026). Refines [0005](0005-actions-and-outbox.md).

## Context

ADR 0005 put write-backs in a transactional outbox and left delivery to a separate service. Three questions were open when it was built:

1. How should the relay claim rows without holding a database transaction open across a network call?
2. How should it record outcomes, given that the audit chain was described as having a single writer, the ontology engine?
3. What database rights should it have, given that the outbox is tenant-scoped and the relay works for every tenant?

## Decision

**Claim with a lease.** One statement selects due rows (`FOR UPDATE SKIP LOCKED`), increments `attempts`, and moves `next_attempt_at` forward by a lease (60 s by default). The transaction then ends. Delivery runs with no transaction open. A worker that dies simply lets its lease run out, and the row becomes due again. A crashed attempt still counts toward `max_attempts`.

**Finalize in one transaction.** Delivered, scheduled for retry, or dead, each is one `UPDATE ... WHERE id = $1 AND status = 'pending'`, plus the audit entry for delivered and dead. If the update touches no row, another worker finished it first (the lease expired while this one was slow) and the late outcome is dropped, with no audit entry. The target may see the idempotency key twice. That is at-least-once, and the lease should exceed the slowest connector timeout so it is rare.

**Retry, then dead.** A `PermanentError` (the target refused: HTTP 4xx other than 408, 425 and 429; or an unconfigured connector) goes dead at once. A `TransientError` retries with capped exponential backoff and jitter until `max_attempts`, then goes dead. A dead row logs at ERROR with `alert=true` until a real alert channel exists. Retries are logged, not audited: an outage would otherwise write one chain entry per row per attempt.

**The executor appends to the audit chain directly.** This replaces the stub README's "through the ontology engine". The chain is enforced by database triggers (per-tenant advisory lock, sequence, hash), not by the engine process, so any role with `INSERT` on `audit_log` appends safely, and doing it in the same transaction as the status change means a delivery and its audit entry cannot disagree. Routing through the engine would add a service-to-service call, a service account and a way for the two to drift. Entries use actor type `service`, actor `s2-action-executor`, and carry the connector, operation, attempt count and error text, never the payload.

**Its own database role.** `s2_executor` has `BYPASSRLS` (it serves every tenant) and nothing else: `SELECT` on the outbox plus `UPDATE` on only `status`, `attempts`, `next_attempt_at`, `delivered_at` and `last_error`; `SELECT` and `INSERT` on `audit_log`; `EXECUTE` on the ontology functions. It cannot read objects or action parameters, change a payload or a connector, or touch an audit entry. On start it refuses to run as a superuser or as a role without `BYPASSRLS` (the second would silently see no rows).

**Connectors.** A connector delivers one write-back and returns or raises `TransientError` or `PermanentError`. Two types ship: `http` (JSON POST with `Idempotency-Key`, an optional bearer token and per-operation path mapping) and `log` (records and succeeds, the default, so the local stack shows the loop with no external system). Connectors are named in `S2_CONNECTORS`, matching the `connector` field in ontology packs. `sap-pm` and `maximo` are adapters over the same interface and are not built.

## Consequences

- Several executors can run side by side; each row is claimed by one at a time.
- The twin runs ahead of the external system by the delivery delay, as ADR 0005 already said. A dead row is the visible symptom, and `make outbox` shows it.
- The executor holds the keys to every tenant's outbox. Its role is narrow, but a compromise of it can mark rows delivered or dead and write audit entries naming it. It cannot rewrite history.
- `BYPASSRLS` needs a superuser to grant. Managed PostgreSQL that withholds it needs the role created by the provider first.
- Alerting is a log line. A dead-letter queue UI, a redrive operation (dead back to pending) and metrics are not built; redrive today is a manual `UPDATE` by the schema owner.
- Actions that must not take effect until the remote system confirms still need the two-step design noted in 0005.
- The `samanvaya-fleet` write-back contract (`ground_vehicle`, `release_vehicle`) is S2's own placeholder, not yet aligned with what Samanvaya 0.5 exposes.
