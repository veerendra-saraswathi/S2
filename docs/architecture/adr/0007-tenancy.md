# 0007. Tenant isolation by row-level security

Status: accepted (November 2026)

## Context

S2 is multi-tenant. The failure to design against is a query that forgets its tenant filter and returns another customer's data. Adding tenancy later means touching every table and every query.

## Decision

- Every table in the `ontology` schema has a `tenant_id` column from the first migration.
- PostgreSQL row-level security is enabled and forced on all of them. The policy compares `tenant_id` with the transaction-local setting `s2.tenant_id`. With the setting absent, no row is visible or writable.
- The engine takes the tenant from the `tenant_id` claim of the verified access token and sets it at the start of every transaction (`db/session.py`).
- Services connect as `s2_app`, which does not own the tables and cannot bypass the policies. Migrations run as the owner. The ontology engine verifies this when it starts and exits if its role is a superuser or has `BYPASSRLS`, so a wrong connection string is a failed deployment rather than a silent cross-tenant leak.
- Names are unique per tenant, so two tenants can define the same object type independently.

## Consequences

- Application code does not filter by tenant, and cannot leak across tenants by forgetting to.
- A component that legitimately works across tenants (the outbox relay, for one) needs its own role with `BYPASSRLS`, granted on only the tables it uses.
- Foreign keys do not include `tenant_id`, so the database does not itself prevent a row in one tenant from referencing a row in another. The row-level policies make such a reference impossible to create through the application role. Composite keys are the stronger form if that ever stops being enough.
- The local Keycloak realm issues one hardcoded tenant. Real multi-tenant identity (for example Keycloak organisations mapped to the claim) is not set up.
- The telemetry database is outside this scheme; see [0008](0008-telemetry-path.md).
