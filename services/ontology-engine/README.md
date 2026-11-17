# Ontology engine

The S2 ontology kernel: the registry of object, link and action types; object and link instances; governed actions; the audit chain. A FastAPI service over PostgreSQL 18 + PostGIS.

It is the only writer of ontology state. Everything else, including future services, changes the twin through this API.

## Layout

```
src/s2_ontology/
  app.py, main.py      application factory and ASGI entry point
  api/                 routers, request and response schemas, auth dependencies
  domain/              registry, objects, actions, property validation, audit
  db/                  SQLAlchemy models, tenant-scoped sessions
  cli.py               s2ctl: token, apply-pack, seed-demo
migrations/            Alembic; 0001 creates the whole `ontology` schema
tests/                 unit tests and integration tests (real PostgreSQL, real OPA)
```

## API

Interactive documentation is served at `/docs`. In outline:

| Area | Endpoints | Who |
|---|---|---|
| Health | `GET /healthz`, `GET /readyz`, `GET /v1/me` | anyone / any token |
| Registry | `POST /v1/ontology/packs:apply` | `ontology-admin` |
| | `GET /v1/ontology/{object-types,link-types,action-types}` | any token |
| Objects | `GET /v1/objects/{type}`, `GET /v1/objects/{type}/{key}` | any token |
| | `PUT` (replace; `If-None-Match: *` to create only), `PATCH` (change named properties), `DELETE /v1/objects/{type}/{key}`. Writes may carry `provenance`, recorded in the audit entry | `ontology-admin` |
| Links | `GET /v1/objects/{type}/{key}/links/{link}` (forward or reverse name) | any token |
| | `POST /v1/objects/{type}/{key}/links/{link}` | `ontology-admin` |
| Time-series | `GET /v1/objects/{type}/{key}/timeseries/{property}` | any token |
| Actions | `POST /v1/actions/{name}/apply` | decided by the action's OPA policy |
| | `POST /v1/action-executions/{id}/approve` | decided by policy |
| | `POST /v1/action-executions/{id}/reject` | `approver` |
| | `GET /v1/action-executions/{id}` | any token |
| Audit | `GET /v1/audit`, `GET /v1/audit/verify` | `auditor`, `ontology-admin` |

"Any token" means any valid access token for the tenant. Reads are also filtered by markings: a caller sees an object type or property only if they hold a `marking:<name>` role for every marking on it ([ADR 0010](../../docs/architecture/adr/0010-markings-on-reads.md)).

## Running it

In the Compose stack this service is `ontology-engine` (and `migrate`, which runs Alembic and exits). To run it from your machine against the stack's databases:

```bash
S2_TELEMETRY_DATABASE_URL=postgresql+psycopg://postgres:postgres_dev@localhost:5433/telemetry \
  uv run uvicorn s2_ontology.main:app --reload --port 8001
```

Configuration is environment variables prefixed `S2_`; see `packages/s2-common/src/s2_common/settings.py`. The defaults there are the stack's addresses as seen from the host.

## Changing the schema

The models in `db/models.py` are the source of truth for tables and indexes. Triggers, row-level security policies and grants are hand-written SQL inside migrations.

```bash
cd services/ontology-engine
export S2_DATABASE_URL=postgresql+psycopg://postgres:postgres_dev@localhost:5432/s2   # the owner, not s2_app
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic upgrade head
```

Read the generated revision before committing it. A new table also needs row-level security: copy the `tenant_isolation` policy from revision 0001.

## Tests

```bash
uv run pytest services/ontology-engine          # unit tests; integration tests skip unless configured
make test-stack                                 # everything, against the running Compose stack
```

The integration tests give every test its own tenant, so they can share one database and they exercise tenant isolation constantly.
