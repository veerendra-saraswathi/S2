# Action executor (Phase 2, not implemented)

Delivers the write-backs that governed actions queue.

When an action executes, `services/ontology-engine` inserts one row per write-back into `ontology.writeback_outbox`, in the same transaction as the ontology change. Nothing sends those rows yet: they accumulate with `status = 'pending'`.

## Contract this service must meet

- **Claim work** with `SELECT ... FROM ontology.writeback_outbox WHERE status = 'pending' AND next_attempt_at <= now() ORDER BY next_attempt_at FOR UPDATE SKIP LOCKED`. The partial index `ix_writeback_outbox_due` exists for this query.
- **Deliver at-least-once.** Send the row's `id` to the target system as the idempotency key.
- **Record the outcome**: `delivered` with `delivered_at`; or increment `attempts`, set `last_error` and push `next_attempt_at` back (exponential backoff); or `dead` when retries are exhausted, which must alert a person.
- **Connectors** are selected by the row's `connector` (`samanvaya-fleet`, `cmms`, later `sap-pm`, `maximo`) and `operation`.
- **Database role**: the outbox is tenant-scoped by row-level security, and this service works across tenants. Give it a dedicated role with `BYPASSRLS` and rights on the outbox table only; do not reuse `s2_app`.
- **Audit** each delivery and each dead letter through the ontology engine, so the chain stays single-writer.

See [ADR 0005](../../docs/architecture/adr/0005-actions-and-outbox.md).
