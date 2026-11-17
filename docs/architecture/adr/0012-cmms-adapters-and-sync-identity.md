# 0012. CMMS adapters look up before they create; the sync deletes only when asked, within a limit, as a service

Status: accepted (November 2026). Refines [0009](0009-action-executor.md) and [0011](0011-lakehouse-catalog-and-sync.md).

## Context

Three things were left open after the executor and the lakehouse shipped:

1. The executor delivered write-backs to a generic webhook only. The specification names SAP and Maximo, and `createWorkOrder` already queues a `create_work_order` write-back for `cmms`.
2. The sync reported objects whose rows disappeared but never removed them.
3. The sync ran as a person (the dev `admin` user), so its audit entries read `actor_type: user` and named that person. That was visible on the first real run.

## Decision

**Two work-order adapters, each translating the ontology's generic payload** (`external_id`, `title`, `priority`, `description`) into the system's own record. Any other operation is a permanent error; an adapter does not guess.

| | IBM Maximo Manage | SAP S/4HANA PM |
|---|---|---|
| API | JSON API, `POST /maximo/api/os/mxapiwo?lean=1`, `apikey` header | OData v2 `API_MAINTNOTIFICATION`, `POST /MaintenanceNotification`, basic or bearer authentication, `sap-client` |
| Record | a work order: site, `description` (100 characters; the full text goes to the long description), `wopriority`, optional work type | a maintenance notification: `NotificationText` (40 characters), `NotificationType`, `MaintPriority` |
| S2's id stored in | `externalrefid` (configurable) | a field you name, often a custom one; none by default |
| Session | none | CSRF token fetched with its session cookie, refetched once when it expires |

**Look up before create.** Delivery is at-least-once (ADR 0009), and neither system honours an idempotency key. So before every create the adapter searches for the record carrying S2's work-order id and does nothing if it finds one. For Maximo that is always on. For SAP it needs a reference field, because no standard notification field is reliably free for it; without one, a retry after a lost response can create a second notification, and the README says so. Ids go into query filters, so only plain identifiers are accepted.

**Priorities are mapped, not passed through.** `CRITICAL/HIGH/MEDIUM/LOW` becomes `1/2/3/4` by default and is configurable per connector. An unmapped priority is a permanent error.

**Deleting absent objects is opt-in and bounded.** `sync --delete-absent` (or `S2_SYNC_DELETE_ABSENT` for scheduled runs) deletes objects that have no row, through the API, with provenance on the audit entry (`DELETE` takes an `S2-Provenance` header). A run that would delete more than 20% of a type's objects is refused before anything is deleted, unless forced. An empty, truncated or wrongly filtered dataset is the usual cause of a mass disappearance, and the ontology should not follow it.

**The sync has its own identity.** Keycloak client `s2-pipeline`, client credentials, a service account holding `ontology-admin` and `marking:internal`, and a hardcoded claim `s2_actor_type: service`. Its writes are audited as `service` and name the service account.

## Consequences

- The adapters are tested against fakes of each API, written from the vendors' documentation. They have not been run against a real Maximo or S/4HANA system; field names and required fields vary between installations (Maximo site and work-type rules, SAP notification types and mandatory fields), so the first real connection will need configuration and probably a mapping change.
- Closing or updating work orders, reading their status back into the ontology, and SAP maintenance orders (as opposed to notifications) are not built.
- A deleted object's links go with it. Deletion is not undone by a later sync that sees the row again; that sync creates a new object, if the dataset supplies every required property.
- The dev realm's client secret is a default (`s2-pipeline-dev-secret`), like the other dev passwords. A stack created earlier needs `make dev-pipeline-client` once, because Keycloak skips the realm import when the realm exists.
