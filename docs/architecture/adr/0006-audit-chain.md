# 0006. Per-tenant hash chain in PostgreSQL

Status: accepted (November 2026)

## Context

The specification asks for an immutable, cryptographically verifiable audit trail of actions, agent tool calls and data changes, for regulatory use. The audit entry for a change must not be able to exist without the change, or the change without the entry.

## Decision

`ontology.audit_log` lives in the same database as the data it describes and is written in the same transaction.

- A `BEFORE INSERT` trigger assigns each entry a per-tenant sequence number, the server's timestamp, the previous entry's hash, and its own hash: SHA-256 over the previous hash and a canonical rendering of the entry. Values a client supplies for those four fields are overwritten.
- Appends are serialised per tenant with an advisory lock. A unique constraint on `(tenant, seq)` means a race can make an insert fail, never fork the chain.
- Triggers reject `UPDATE`, `DELETE` and `TRUNCATE` for every role. The application role is additionally granted only `SELECT` and `INSERT`.
- `ontology.audit_verify(tenant)` re-walks a chain and reports the first entry that does not check out. One SQL function computes the hash for both writing and verifying.
- Chains are per tenant so that a tenant's log can be exported and verified on its own.

## Consequences

- An edit, a reordering or a gap in the history is detectable, including one made by a superuser who disables the triggers.
- **Removing the newest entries is not detectable from inside the database.** Tamper-evidence becomes complete only when the chain head is periodically written somewhere the database administrator cannot rewrite (an object-lock bucket, a transparency log, a customer-held ledger). That anchoring is not built; it is planned with the Phase 4 audit hardening. Until then the honest description is "tamper-evident against edits", not "immutable".
- Verification depends on PostgreSQL's JSONB text rendering. An external verifier would need the same canonical form; if one is required, switch the hashed rendering to RFC 8785 (JCS).
- Audit appends for one tenant are sequential. That is fine for actions and administration. Auditing every read would need batching or a different store.
