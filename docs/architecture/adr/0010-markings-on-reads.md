# 0010. Markings are enforced on reads in the engine, all-of, from realm roles

Status: accepted (November 2026).

## Context

Object types and properties have carried security markings since Phase 1 (`markings: [internal]` on `CommercialDrone.actuatorHealth`), but nothing read them: any user of a tenant could read every object, property, series and audit value in it. Tenant isolation (ADR 0007) is a wall between customers; markings are the walls inside one.

Four questions:

1. Where does a user's clearance come from?
2. What does holding several markings mean?
3. What does a user see when they lack one?
4. Where is it enforced?

## Decision

**Clearance is a realm role named `marking:<name>`.** Roles already travel in the token (`realm_access.roles`) and are what every S2 check reads, so no new claim, mapper or user-profile attribute is needed, and granting a clearance is the same act as granting any role. The cost is that markings and roles share a namespace; the `marking:` prefix keeps them apart.

**All-of.** To see something, a user must hold every marking on it. A property's effective markings are its own plus its type's. A marking can only narrow who sees something, never widen it, so adding one is always safe.

**Absence, not refusal, on reads.**

| The user lacks a marking on | Reads | Writes (ontology-admin) |
|---|---|---|
| the object type | 404, with the same message as an unknown type: objects, links to or from it, its series | 404 |
| a property | left out of every object returned, including through links; its series 404s as if it did not exist | writing it is 403; a full replace (PUT) keeps its value instead of clearing it |

A 403 would confirm the thing exists. The write case is different: the definitions are visible (below), so refusing the write reveals nothing new, and silently dropping the value would be worse.

**Audit entries are redacted on read.** Change records in `details` lose the values of properties the reader is not cleared for; a record about a type they are not cleared for keeps only its `op`, and the entry's `resource_id` is withheld. The entry says `redacted: true`. The stored entry and its hash are untouched, so `GET /v1/audit/verify` still checks the whole chain, but a redacted entry cannot be re-hashed by the client.

**Definitions stay readable.** `/v1/ontology/...` shows every type and property, with their markings, to everyone in the tenant. It is the schema, not the data, and the people who write packs need to see it whole.

**Enforced in the engine, in Python, not in OPA and not by row-level security.** The decision is a subset test on data already loaded with the type definition; a policy round-trip per object would cost more than the read. PostgreSQL row-level security cannot drop keys from a JSONB document. The rule lives in one module (`domain/markings.py`) that every object and audit route calls.

**The primary key cannot be marked.** It would hide an object's identity from people who can see the object; mark the type instead. Packs that try are rejected.

## Consequences

- The dev realm gives `marking:internal` to `admin`, `operator`, `supervisor` and `auditor`; `approver` and `viewer` do not have it. Keycloak skips the realm import when the realm already exists, so a stack created earlier needs `make dev-markings` once, and new tokens.
- **Actions are not filtered.** An action's effects are defined by an ontology admin and run with the platform's authority once policy allows them; the caller does not see the changed values unless cleared (the response carries parameters, not object state). A policy that should depend on clearance can read the roles in its input.
- **Not covered yet:** telemetry read straight from TimescaleDB, which has no row-level security either (a known gap); the outbox payload of a write-back, which the executor delivers whatever its markings; action parameters, which have no markings; search and aggregation, which do not exist yet; Workshop UI, which reads through the API and so inherits all of this.
- Markings are free-form strings. A typo in a pack creates a marking nobody holds and hides the data from everyone, which is the safe failure.
- Every read now loads the type's properties to decide visibility; it already did, to build the response.
