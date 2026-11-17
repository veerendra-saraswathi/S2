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

## Phase 2, slice 1: the action executor (17 November 2026)

Written and tested in a sandbox with **no Docker daemon, no Keycloak, and PostgreSQL 16 without PostGIS**.

| What | How it was run |
|---|---|
| Executor tests (51) | Connector tests with no network (httpx mock transport). Relay tests against a real PostgreSQL 16, migrated with the engine's Alembic revisions from a copy patched for the missing PostGIS (`geometry` as `bytea`, no GiST index) and with a stand-in `uuidv7()`. Includes two and four workers racing on 40 rows, a crashed worker, a late finish after lease expiry, tenant audit chains verified with `audit_verify`, and what `s2_executor` cannot do |
| Tests catch regressions | Broken on purpose: no lease (3 tests fail), no `status = 'pending'` guard on finalize (1 fails), no audit entry on dead letter (2 fail). Removing `SKIP LOCKED` fails nothing: the lease makes correctness independent of it, and it only prevents workers queueing on locks (see the comment on `CLAIM_SQL`) |
| Migration 0002 | `upgrade`, `downgrade`, `upgrade` on the patched copy; `alembic check` shows only the two stubbed PostGIS items |
| Compose file | `docker compose config -q` passes |
| Lint and format | `ruff check`, `ruff format --check` |

**Not run:** the executor container and its Dockerfile, the Compose wiring (initdb creating `s2_executor`, the password variable, `depends_on`), `make outbox` and `make executor-role`, anything against PostgreSQL 18 with real PostGIS (CI will be the first), the `http` connector against a real server, the Helm chart (the executor has none), and the engine's own test suite after migration 0002 (CI will be the first).

First boot, in order: `make up` (watch `docker logs s2-action-executor`: it must start, not exit on the role check), `make seed`, run the `groundDrone` request from the README, `make outbox` (expect `delivered`), `curl .../v1/audit?event_type=writeback.delivered` as the auditor, `GET /v1/audit/verify`, then `make test-stack`.

### Slice 1 on the real stack (17 November 2026)

Run by Veerendra on Docker Desktop for macOS, on the existing Phase 1 volumes. The executor image built; `make executor-role` gave the existing database's `s2_executor` a login (it must run after `make up`: the first attempt failed with the stack down); the executor passed its role check on PostgreSQL 18 and delivered the two write-backs left pending since Phase 1, then a new `groundDrone` write-back about 0.4 s after the action committed, each on the first attempt (`make outbox`). The first `make up` failed on Kafka and Keycloak health checks because three kind clusters in the same Docker were using most of the CPU; with them stopped, Kafka was healthy in 46 s.

Also now run, in the sandbox with PostGIS installed: the ontology engine's full suite with migration 0002 applied (65 tests), on PostgreSQL 16 with a stand-in `uuidv7()`.

## Phase 2, slice 2: markings on reads (17 November 2026)

Sandbox: PostgreSQL 16 with PostGIS 3, OPA 1.20.2, no Docker, no Keycloak.

| What | How it was run |
|---|---|
| Engine suite (80, of which 15 new in `test_markings.py`) | Real PostgreSQL and OPA, tokens from a throwaway key, as for the rest of the suite |
| Tests catch regressions | Broken on purpose: no type check (3 fail), no preserve on replace (1), any-of instead of all-of (1, after making the property path use the same rule), no far-end check on links (1), audit `resource_id` not withheld (1). Dropping the type's markings from the series check fails nothing: that path checks the type first, so the case cannot arise |
| `make dev-markings` | Script expanded with `make -n` and checked with `bash -n` |
| Whole repository from the root (`pytest`, as `make test` and CI run it) | 178 passed, including the executor's relay tests against the engine's real migrations with PostGIS. This found a slice-1 bug: `services/action-executor/tests/__init__.py` made a second `tests` package whose `conftest` clashed with the engine's, so a root run failed before collecting anything. Removed |

**Not run:** anything against Keycloak, including the new realm role and assignments in `s2-realm.json` and `make dev-markings` itself; the README walkthrough's viewer `actuatorHealth` call.

First boot: `make dev-markings`, then `make token AS=operator` and `AS=viewer`, then the walkthrough: the operator reads `actuatorHealth`, the viewer gets 404.

### Slice 2 on the real stack (17 November 2026)

Run by Veerendra on the same Mac. `make dev-markings` created `marking:internal` in the existing Keycloak and granted it; with fresh tokens, the operator read DRN-0003's `actuatorHealth` series (live points from the simulator) and the viewer got the 404 of a property that does not exist. A first `make up` failed because Docker Desktop could not resolve Docker Hub; it passed once the network was back.

## Phase 2, slice 3: the lakehouse (17 November 2026)

Sandbox: PostgreSQL 16 with PostGIS 3, OPA 1.20.2, SeaweedFS 4.48 (the release binary, same version as the stack), Dagster 1.13, PyIceberg 0.12. No Docker, no Keycloak.

| What | How it was run |
|---|---|
| Pipeline tests (31) | Catalog, branches, promotion, typed columns on a SQLite catalog; planning with no I/O; CLI; Dagster job in-process; end to end with the real engine in-process on migrated PostgreSQL, called over HTTP; the catalog in PostgreSQL as `s2_app`; the lakehouse on SeaweedFS over S3 |
| Engine tests (88) | Adds `PATCH`, provenance, create-only `PUT` |
| SeaweedFS | Found and fixed: with its single-server defaults a second bucket's uploads fail (`INTERNAL_FAILURE`, no writable volumes). With `-volume.max=100 -master.volumeSizeLimitMB=256`, as Compose now runs it, several buckets work |
| CLI with the Compose configuration | `demo-load`, branch create, `demo-rollout`, promote, and a refused promote after `main` moved, with the catalog in a migrated PostgreSQL as `s2_app` and files on SeaweedFS |
| Dagster | `dagster definitions validate`; `dagster dev` booted and its GraphQL API returned the asset graph `lakehouse/assets/commercial_drone` -> `ontology/CommercialDrone` |
| Migration 0003 | Applied on top of 0001-0002 in every database the suites create |

**Not run:** the `s2/pipelines` image and its Compose service; `make lakehouse`, `make lakehouse-demo`, `make pipelines-ui`; the sync against Keycloak tokens (its token code is the same password grant the engine CLI uses); SeaweedFS with the new flags inside Docker; Dagster's schedule firing.

First boot: `make up` (runs migration 0003; recreates the object store with the new flags, keeping its data), `make seed` (core-fleet 0.2.0 adds the column mapping), `make lakehouse-demo` (expect `"updated": 3`), `make lakehouse-demo` again (expect `"unchanged": 3`), then the branch walkthrough in `services/pipeline-orchestrator/README.md`, then `make pipelines-ui`.

### Slice 3 on the real stack (17 November 2026)

Run by Veerendra on the same Mac. The pipelines image built; `make lakehouse-demo` wrote the register to Iceberg on SeaweedFS and updated the three drones (serial numbers, firmware), and a second run reported `"unchanged": 3`. The branch walkthrough worked as written: the dry run on `preview` showed exactly one change (DRN-0003 firmware 4.2.0), promotion moved main to the branch snapshot, and the sync updated one object. The audit entry for that change carried the dataset, branch, snapshot and run id. It also showed the sync acting as the `admin` user (`actor_type: user`), which slice 4 fixes.

## Phase 2, slice 4: CMMS adapters, bounded deletes, the pipeline's identity (17 November 2026)

Sandbox as for slice 3.

| What | How it was run |
|---|---|
| Maximo and SAP PM adapters (14 tests) | Against fakes of each API, written from IBM's and SAP's documentation: Maximo's API key, OSLC lookup and create; SAP's CSRF token bound to a session cookie, an expired token refetched once, the reference-field lookup. Broken on purpose: no lookup before create (1 fails), no token refetch (1 fails) |
| Executor suite (65) | Also found: the outbox-cleanup fixture was autouse and needed the database, so without one it silently skipped the 22 connector tests as well. Now only database tests use it |
| Absent deletes | End to end with the real engine: off by default; refused above 20% before anything is deleted; forced deletion audited with provenance; an emptied dataset cannot wipe the type |
| Service-account identity | A token with `s2_actor_type: service` makes the sync's audit entries read `service`; the token source uses client credentials and says to run `make dev-pipeline-client` when the client is missing |
| `make dev-pipeline-client` | Script expanded with `make -n` and checked with `bash -n`; realm JSON parses |

**Not run:** the adapters against a real Maximo or S/4HANA; the `s2-pipeline` client in Keycloak (realm import or `make dev-pipeline-client`); `make lakehouse` with `--quiet` builds.

First boot: `git am`, `make up`, `make dev-pipeline-client`, `make lakehouse-demo` (expect `"unchanged": 3`), then the audit query from the README: new entries read `actor_type: service`.

### Slice 4 on the real stack (17 November 2026)

Run by Veerendra: `make dev-pipeline-client` added the service account; a firmware change through branch `fw43`, promoted and synced, was audited with `actor_type: service` and the service account's id, with full provenance.

## Phase 3, slice 1: the Workshop UI (17 November 2026)

Sandbox: the engine (uvicorn) on PostgreSQL 16 + PostGIS with OPA and a plain-PostgreSQL telemetry table; Keycloak replaced by a local signing key served as JWKS, with the browser session injected the way `oidc-client-ts` stores it; Google Chrome headless driven by Playwright.

| What | How it was run |
|---|---|
| UI tests (20, Vitest + Testing Library, jsdom) | API client and error parsing, roles from tokens, helpers, chart geometry, parameter coercion; the object page (properties, links, series hidden by marking, action with the key filled in, approval-needed result, policy refusal with reasons, client-side validation), the approval queue (own request cannot be approved, rejection needs a reason, non-approvers see no buttons), explorer and type pages |
| Engine test (1 new, 91 total) | `GET /v1/action-executions`: order, filters, validation, tenant isolation |
| In a real browser, against the real engine and OPA | Every page screenshotted as operator, viewer, approver and admin, on the dev server and on the production build served by `vite preview` (the container's command). Clicked through: the approver approved the operator's return-to-service; the drone came back `READY`; the operator grounded it again from its page; a viewer was refused by policy and the reason shown. The marked actuator series showed for the operator and "not available" for the viewer |
| Found and fixed | MapLibre 6's worker failed to load under Vite (the map drew no data); fixed by bundling the worker and `setWorkerUrl`, checked on the dev server and the production build. MapLibre was 1.1 MB of the main bundle; the map page now loads on demand |
| Container | Its install and build steps replayed from a clean copy; `docker compose config` passes |

**Not run:** sign-in through Keycloak (the realm's `s2-workshop` client, PKCE, redirect to `/callback`); the `s2/workshop-ui` image in Docker; map tiles (the sandbox cannot reach OpenStreetMap: the map drew its markers on a blank background).

First boot: `make up` (builds and starts `s2-workshop-ui`), open <http://localhost:5173>, sign in as `operator`.

### Phase 3 slice 1 on the real stack (17 November 2026)

Run by Veerendra: the `workshop-ui` image built and served; Keycloak sign-in through `s2-workshop` worked; a grounding made in the UI was delivered by the executor (`make outbox`); sign-in returned to a deep link; the audit page verified the chain and showed lakehouse provenance; the approval queue enforced four-eyes.

## Phase 3, slice 2: triage queues (17 November 2026)

| What | How it was run |
|---|---|
| Engine tests (18 new, 108 total) | Rules applied from the pack and idempotently; ten kinds of invalid rule refused with their reason; property rules; series rules against a real telemetry table: latest reading wins, stale readings do not count, grounding takes a drone out of one queue and into another; counts; markings hide a rule over a marked series; no telemetry store gives a warning; tenants see only their own. Broken on purpose: no freshness cutoff (1 fails), no marking check (1 fails) |
| UI tests (4 new, 24 total) | Queue ordering, evidence in words, the busiest urgent queue opened with its evidence and actions, acting from the queue, switching queues |
| In a browser, against the real engine | Migration 0004 applied to the database from slice 1; the pack's three rules created; with fresh telemetry the operator saw Garuda-1 in Degrading actuators with its evidence and the urgent count in the navigation, grounded it from the queue, and found it in Grounded drones; the viewer had no actuator queue |

**Not run:** on the Compose stack (needs `make seed` for core-fleet 0.3.0 and `make sim` for live telemetry).

## Phase 4, slice 1: governed agents (17 November 2026)

| What | How it was run |
|---|---|
| Engine tests (2 new, 110 total) | An agent without a rationale gets 422; the rationale is stored on the execution, in the audit entry and in lists; the person an agent acts for cannot approve its proposal, another approver can; people may give a rationale too |
| Policy tests (4 new, 26 total) | Approval by the person an agent acts for is denied, whether matched by subject or username; another approver is allowed; a person's own request is unaffected |
| Agent tests (11) | Against the real engine, OPA and a telemetry table, with a scripted model: reads a trend and proposes, and the proposal lands for approval and changes nothing; the supervisor cannot approve, another approver can and the drone is grounded; no second proposal while one awaits a decision, none within the cooldown after a rejection; another action or another object cannot be chosen; dismiss; no decision within the step limit; Ollama down, an engine refusal and an empty rationale reported as failures; proposal limit; unknown tool; markings hide the actuator queue from an agent without `marking:internal`. Ollama client tested against a fake HTTP server |
| UI tests (2 new, 26 total) | Agent badge, "on behalf of" and rationale shown; the person the agent acts for sees Approve disabled; another approver approves |

**Not run:** against a real model (Ollama could not be downloaded in the build sandbox), or on the Compose stack. Needs `make seed` (core-fleet 0.4.0), `make dev-agent-client`, `ollama pull llama3.1`, `make sim`, `make agent-once`.
