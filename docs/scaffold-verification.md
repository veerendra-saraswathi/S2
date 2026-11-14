# What was verified when this scaffold was generated

The scaffold was written and tested on 14 November 2026 in a sandbox that had **no Docker daemon, no container registry access, and PostgreSQL 16 without PostGIS or TimescaleDB**. The OPA 1.20.2 binary was available. This page records what was run there, with which substitutes, and what is therefore still untested. Delete it once the first-boot checklist below has passed and CI is green.

## Run and passing

| What | How it was run |
|---|---|
| Python test suite (112 tests) | Against a real PostgreSQL 16, migrated with Alembic and queried as the non-owner role, and a real OPA 1.20.2 serving the repository's policies. `uuidv7()` and the PostGIS functions were stubbed; the geometry column was a `bytea` |
| Safeguards catch regressions | Three were broken on purpose: the service connects as a superuser with its startup guard removed; the policy client always allows; successful actions are not audited. Each made tests fail (10, 19 and 4 failures) |
| Migration | `upgrade`, `downgrade`, `upgrade` again. `alembic check` reported no drift between the models and the migrated schema apart from the two stubbed PostGIS items |
| Policy tests | `opa test policies` (22 cases) and `opa check --strict` |
| Telemetry pipeline | The real `telemetry-sim`, `mqtt-bridge` and `timeseries-sink` processes, with a Python MQTT broker and librdkafka's mock cluster, writing to PostgreSQL 16. Replay of the whole topic changed nothing; a malformed message reached the dead-letter topic |
| Vertical slice | Ingested telemetry read back through the API before and after the object was registered, a viewer refused by policy, an operator's action applied and queued for write-back, the audit chain verified |
| Dockerfile install steps | Each Dockerfile's `COPY` + `uv sync --frozen --package ...` replayed in a clean directory |
| Lint and format | `ruff check`, `ruff format --check`, `opa fmt` |
| Workshop UI | `tsc --noEmit` and `vite build` |
| Compose file | Parsed by `docker compose config` (v5.7) |
| CI workflow | Checked with `actionlint`; not executed |
| Database init script | Run with `psql` the way the Postgres entrypoint runs it |

### A defect the policy tests caught

The first run of `opa test` failed one case: the baseline action policy **allowed malformed input**. An empty input document produced `allow = true`, because rules of the form `not "role" in input.actor.roles` are undefined, not denials, when the field is missing. The engine always sends a complete document, so no request through the API could reach this, but the policy claimed to fail closed and did not.

It is fixed in `policies/s2/actions/common.rego` (helper rules, a `well_formed` requirement in every `allow`) and covered by eight new test cases. The trap and how to avoid it are described in [policies/README.md](../policies/README.md#make-missing-input-deny). Treat it as a reason to run `opa test` on every policy change, which CI now does.

## Not run

Nothing below has been executed. Each is a place the first boot can fail.

- **Any container.** Image tags, entrypoints, health checks and the Dockerfiles as a whole.
- **PostgreSQL 18, PostGIS and TimescaleDB.** Native `uuidv7()`, real geometry storage and reads (`ST_GeomFromGeoJSON`, `ST_AsGeoJSON`), `create_hypertable`.
- **The OPA container** with the flags in the Compose file. The binary itself was used for every policy decision in the tests.
- **Keycloak.** The realm import, the token claims (audience and tenant mappers) and the issuer / back-channel hostname settings.
- **Mosquitto, Kafka and SeaweedFS** with the configuration in `deploy/docker-compose`.
- **The Helm chart.** Not linted, templated or installed.
- **The GitHub Actions workflow** as a run.
- **The Workshop UI in a browser.** It builds; nobody has looked at it.

## First-boot checklist

Work through these in order; each step isolates a different set of unknowns.

1. `make up`. If a service is unhealthy, `make ps` shows which and `make logs` or `docker logs s2-<name>` shows why. Likely suspects: an image tag that does not exist (edit `deploy/docker-compose/.env`), the Keycloak health check, the Kafka listener settings.
2. `curl -s -H "Authorization: Bearer $(make -s token AS=operator)" http://localhost:8000/v1/me`. It should print the tenant and the roles `operator` and `viewer`. A 401 names the claim that is wrong (audience, issuer, tenant); the fix is then in the realm import (`deploy/docker-compose/keycloak/s2-realm.json`) or the `S2_OIDC_*` settings in the Compose file.
3. `make seed`. This exercises PostgreSQL 18, PostGIS writes and the token end to end.
4. `make sim`, wait a few seconds, then run the time-series `curl` from the README. This exercises Mosquitto, Kafka and TimescaleDB.
5. The `groundDrone` `curl` from the README. This exercises the OPA container.
6. `make test-stack`. This runs the whole suite on the real stack, with nothing stubbed.
7. Push and watch the first CI run.

## First boot on real infrastructure (15 November 2026)

The Compose stack was started on Docker Desktop for macOS (Intel) and the Phase 1 slice was exercised by hand. These passed:

| What | Result |
|---|---|
| `make up` | All services up; every service with a health check reported healthy |
| `make seed` | Packs applied and demo objects created, on PostgreSQL 18 with real `uuidv7()` and PostGIS |
| `make sim` and the ingestion path | Readings and positions accumulated in TimescaleDB |
| Keycloak tokens | `make token` for `operator`, `approver` and `auditor`; the API accepted them |
| `groundDrone` | Succeeded; a repeat was denied by policy with its reason |
| `returnDroneToService` | Pending approval, then approved by a second user and applied |
| `GET /v1/audit/verify` | Verified at 16 entries after seeding and at 20 after the actions |

Four defects were found and fixed during that boot: images whose files were unreadable by the non-root service user, a PostgreSQL health check that could pass during initialisation, a Kafka health check too tight for a busy machine, and `make token` defaulting to a user named `as`.

Still not run: `make test-stack`, the time-series read endpoint, the Helm chart, and the GitHub Actions workflow.
