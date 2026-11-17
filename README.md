<div align="center">

# Samanvaya 2 (S2)

**An open-source enterprise data operating system and operational digital twin for civilian industry**

[![CI](https://github.com/veerendra-saraswathi/S2/actions/workflows/ci.yml/badge.svg)](https://github.com/veerendra-saraswathi/S2/actions/workflows/ci.yml)
![Licence](https://img.shields.io/badge/licence-Apache--2.0-2ea44f)
![Status](https://img.shields.io/badge/status-Phase%202%20of%204%20in%20progress-f59e0b)
![Python](https://img.shields.io/badge/python-3.13-3776ab?logo=python&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-18%20%2B%20PostGIS-336791?logo=postgresql&logoColor=white)
![Kafka](https://img.shields.io/badge/Apache%20Kafka-KRaft-231f20?logo=apachekafka&logoColor=white)
![OPA](https://img.shields.io/badge/policy-Open%20Policy%20Agent-7d9199)

[What it is](#what-it-is) · [Architecture](#architecture) · [Terminology](#terminology) · [Tech stack](#tech-stack) · [Quick start](#quick-start) · [Roadmap](#roadmap)

</div>

## What it is

Industrial operators hold their picture of the world in pieces: drone and robot telemetry in one place, asset records in an ERP, work orders in a CMMS, measurements in SCADA, maps in a GIS. S2 joins those pieces into one typed model of the business, called an **ontology**, that people and software can query, act on and audit.

It is built for civilian commercial work: utility grid inspection, smart manufacturing and warehouse logistics, civil infrastructure monitoring, precision agriculture.

```mermaid
flowchart LR
    subgraph SRC["Where the data lives today"]
        direction TB
        s1["UAV and UGV telemetry"]:::src
        s2["ERP and CMMS records"]:::src
        s3["SCADA and sensors"]:::src
        s4["GIS and imagery"]:::src
    end

    subgraph S2P["Samanvaya 2"]
        direction TB
        p1["Ingest and store"]:::ingest
        p2["Ontology: objects, links, actions"]:::core
        p3["Policy, tenancy and audit"]:::gov
        p1 --> p2
        p3 -.- p2
    end

    subgraph USE["Who uses it"]
        direction TB
        u1["Operators and supervisors"]:::use
        u2["Applications and dashboards"]:::use
        u3["AI agents, propose only"]:::use
    end

    SRC --> p1
    p2 --> USE
    USE -->|"governed actions"| p2
    p2 -->|"write-back"| s2

    classDef src fill:#475569,stroke:#1e293b,color:#ffffff
    classDef ingest fill:#0891b2,stroke:#155e75,color:#ffffff
    classDef core fill:#2563eb,stroke:#1e3a8a,color:#ffffff
    classDef gov fill:#b45309,stroke:#78350f,color:#ffffff
    classDef use fill:#15803d,stroke:#14532d,color:#ffffff
```

Samanvaya 0.5 and 1 run the fleet itself: field telemetry and edge autonomy for commercial UAVs and UGVs. S2 sits above them. The telemetry contract in this repository is a placeholder of S2's own and has not yet been aligned with Samanvaya 0.5.

Three ideas carry the design:

| | Idea | What it means in practice |
|---|---|---|
| 🧩 | **Model the business, not the tables** | A drone, a substation and the fact that one inspects the other are first-class, typed things with an API, whatever systems the data came from. |
| 🛡️ | **Every change is a governed action** | Nobody edits state directly. A change is a named action with validated parameters, a policy decision, and where required a second person's approval. |
| 🔗 | **Everything is on the record** | Each action, approval and denial is written to an append-only, hash-chained audit log that can be re-verified at any time. |

## Status

Phase 3 of 4 is under way. Phases 1 and 2 are done. Phase 1 (foundation and ontology kernel): one vertical slice works end to end and has been run on a real Docker stack: telemetry in, objects and links out, a policy-checked action with two-person approval, and a verified audit chain. Phase 2 has delivered the action executor, which delivers the write-backs those actions queue, read-side access control from security markings, and the lakehouse: Iceberg datasets with branches, synced into objects with per-value provenance.

| Area | State |
|---|---|
| Ontology engine: registry, objects, links, governed actions, audit chain | 🟢 Built and tested |
| Ingestion: MQTT bridge, Kafka log, TimescaleDB sink, telemetry simulator | 🟢 Built and tested |
| Policies (OPA / Rego) for actions, with four-eyes approval | 🟢 Built and tested |
| Local stack (Docker Compose) | 🟢 Boots and runs the slice on Docker Desktop for macOS |
| Helm chart (ontology engine only) | 🟡 Written, not yet linted or installed |
| Workshop UI | 🟡 Built and tested, and clicked through in a browser against the real engine: ontology explorer, objects with live series, map, actions, approvals, audit. Keycloak sign-in not yet run (Phase 3) |
| Action executor (write-back delivery) | 🟢 Built, tested, and delivering on the Compose stack: leased claims, retry with backoff, dead letters, audit, own DB role, `http`, `log`, IBM Maximo and SAP PM connectors. The Maximo and SAP adapters are tested against API fakes, not yet a real system (Phase 2) |
| Read-side access control (markings) | 🟢 Built, tested, and enforced on the Compose stack: objects, links, series and audit entries filtered by `marking:` roles (Phase 2) |
| Lakehouse: Iceberg datasets, branches, sync into objects, Dagster lineage | 🟢 Built, tested, and run on the Compose stack: sync, idempotent re-sync, branch preview and promote, provenance in the audit log. Bounded deletes and the service account are not yet run there. No dbt or incremental sync yet (Phase 2) |
| AI, simulation and governed agents | ⚪ Stub (Phase 4) |

What was and was not verified, and how, is recorded in [docs/scaffold-verification.md](docs/scaffold-verification.md).

## Architecture

The [specification](docs/architecture/spec.md) describes seven layers. This is where each one stands today.

| # | Layer | In this repository | State |
|---|---|---|---|
| 1 | Ingestion and connector fabric | `services/ingestion-connectors` | 🟢 MQTT telemetry path. CDC, batch and ERP connectors come in Phase 2 |
| 2 | Lakehouse and lineage | `services/pipeline-orchestrator` | 🟡 Iceberg datasets and branches, sync into objects with provenance, Dagster lineage |
| 3 | Operational ontology | `services/ontology-engine`, `ontologies/` | 🟢 Object types, properties, links, actions, instances |
| 4 | Time-series, geospatial, media | TimescaleDB and PostGIS, read through the engine | 🟡 Time-series and one geometry per object. Media indexing later |
| 5 | AI, simulation, governed agents | `services/ai-simulation` | ⚪ Schema and policies already treat agents as propose-only |
| 6 | Workshop application builder | `apps/workshop-ui` | 🟡 Shell |
| 7 | Security, access control, audit | `packages/s2-common`, `policies/`, engine | 🟡 Login, roles, policy on actions, tenant isolation and audit chain. [Known gaps](docs/architecture/overview.md#security-model-and-known-gaps) |

### What runs today

Two paths meet at the ontology engine. Telemetry flows in from the field and lands in TimescaleDB. People and software act through the engine, which is the only writer of ontology state.

```mermaid
flowchart LR
    sim["telemetry-sim"]:::ingest -->|MQTT| mqtt[("Mosquitto")]:::ingest
    mqtt --> bridge["mqtt-bridge"]:::ingest
    bridge -->|"s2.telemetry.raw"| kafka[("Kafka")]:::ingest
    kafka --> sink["timeseries-sink"]:::ingest
    sink --> tsdb[("TimescaleDB")]:::store

    user(["Operator"]):::use -->|"log in"| kc["Keycloak"]:::gov
    user -->|"REST + token"| engine["ontology-engine"]:::core
    engine -->|"allow or deny"| opa["OPA"]:::gov
    engine -->|"ontology, audit, outbox"| pg[("PostgreSQL + PostGIS")]:::store
    engine -->|"time-series reads"| tsdb
    pg -->|"outbox"| exec["action-executor"]:::ingest
    exec -->|"write-backs"| ext(["ERP, CMMS, fleet"]):::planned

    classDef ingest fill:#0891b2,stroke:#155e75,color:#ffffff
    classDef core fill:#2563eb,stroke:#1e3a8a,color:#ffffff
    classDef gov fill:#b45309,stroke:#78350f,color:#ffffff
    classDef store fill:#6d28d9,stroke:#4c1d95,color:#ffffff
    classDef use fill:#15803d,stroke:#14532d,color:#ffffff
    classDef planned fill:#94a3b8,stroke:#475569,color:#0f172a,stroke-dasharray: 5 5
```

Colour key: teal is ingestion, blue is the ontology engine, amber is identity and policy, violet is storage, green is a person, grey dashed is planned.

### What happens when someone requests an action

```mermaid
sequenceDiagram
    autonumber
    actor C as Caller
    participant E as ontology-engine
    participant O as OPA
    participant D as PostgreSQL

    C->>E: POST /v1/actions/groundDrone/apply
    E->>E: Validate parameters (JSON Schema)
    E->>D: Read the target object
    E->>O: May this actor do this, to this object, now?
    alt Denied
        rect rgba(220, 38, 38, 0.12)
            E->>D: Audit the denial
            E-->>C: 403 with the reasons
        end
    else Allowed, approval required
        rect rgba(245, 158, 11, 0.15)
            E->>D: Record as pending approval, audit the request
            E-->>C: 202, waiting for a second person
        end
    else Allowed
        rect rgba(22, 163, 74, 0.12)
            E->>D: Apply edits, queue write-backs, audit (one transaction)
            E-->>C: 200
        end
    end
```

- **One transaction.** The edits, the queued write-backs and the audit entry commit together or not at all.
- **One policy decision.** "May this person do it" and "may it be done to this object now" are the same OPA query, and its decision id is stored with the audit entry.
- **Fail closed.** If OPA is unreachable or returns anything other than a clean allow, nothing executes.
- **Approval is decided again.** A pending action is re-evaluated when approved, against the current state, and the requester cannot approve their own request.
- **Agents propose.** An action open to AI agents must require human approval.

More detail is in the [architecture overview](docs/architecture/overview.md) and the [decision records](docs/architecture/adr/README.md).

## Terminology

The example below is the demo data that `make seed` loads.

```mermaid
flowchart LR
    drone["<b>CommercialDrone</b><br/>DRN-0001<br/>callSign: Garuda-1<br/>status: READY<br/>position: geopoint<br/>batteryVoltage: timeseries"]:::obj
    sub["<b>Substation</b><br/>SS-HYD-07<br/>voltageKv: integer<br/>location: geopoint"]:::obj
    act{{"<b>groundDrone</b><br/>parameters: droneId, reason<br/>policy: s2/actions/ground_drone"}}:::act
    log[("Audit log<br/>hash-chained")]:::audit

    drone -->|"inspects"| sub
    sub -.->|"inspectedBy"| drone
    act ==>|"sets status to GROUNDED"| drone
    act -->|"records"| log

    classDef obj fill:#2563eb,stroke:#1e3a8a,color:#ffffff
    classDef act fill:#b45309,stroke:#78350f,color:#ffffff
    classDef audit fill:#6d28d9,stroke:#4c1d95,color:#ffffff
```

| Term | Meaning |
|---|---|
| **Ontology** | The typed model of the business: what kinds of things exist, how they relate, and what may be done to them. |
| **Object type** | A class of thing, such as `CommercialDrone` or `Substation`. Each real drone or substation is an **object** of that type, identified by its primary key. |
| **Property** | A typed field on an object type: text, number, date, a map point or shape, a media reference, or a time-series. A time-series property is a pointer to a metric in TimescaleDB, not a stored value. |
| **Link type** | A named relationship between two object types, readable in both directions (`inspects` / `inspectedBy`). |
| **Action type** | A governed change: a schema for its parameters, the edits it makes, the write-backs it queues, and the policy that guards it. |
| **Pack** | Ontology as code. A YAML file under [`ontologies/`](ontologies/README.md) that defines object, link and action types and is applied through the API. |
| **Governed action** | An action request that has passed parameter validation and a policy decision, and has been audited. This is the only way ontology state changes. |
| **Four-eyes approval** | Some actions need a second person. The requester cannot approve their own request. |
| **Policy** | A rule written in Rego and evaluated by OPA that allows or denies an action, with reasons. |
| **Write-back** | A message to an external system (ERP, CMMS, fleet) produced by an action. It is queued in an **outbox** in the same transaction as the change, to be delivered afterwards. |
| **Audit chain** | An append-only log in which each entry carries a hash of the one before it, so any edit, reordering or gap is detectable. |
| **Tenant** | One customer organisation. PostgreSQL row-level security keeps each tenant's ontology data invisible to the others. |
| **Operational digital twin** | The live, queryable picture of physical assets that results: current state, history, position and relationships. |

## Tech stack

| Concern | Technology | Role in S2 |
|---|---|---|
| API and domain logic | Python 3.13, FastAPI, Pydantic, SQLAlchemy 2, Alembic | The ontology engine and its database migrations |
| Ontology store | PostgreSQL 18 with PostGIS | Definitions, objects (JSONB), links, geometry, audit log, outbox |
| Time-series | TimescaleDB | Telemetry readings and positions |
| Field messaging | MQTT (Eclipse Mosquitto locally) | Telemetry from vehicles and sensors |
| Event log | Apache Kafka (KRaft, no ZooKeeper) | Durable, replayable log between ingestion and storage |
| Identity | Keycloak (OpenID Connect) | Login, tokens, roles, tenant claim |
| Policy | Open Policy Agent, Rego | Allow or deny on every action |
| Object storage | SeaweedFS (S3-compatible) | Iceberg data and metadata files; later, inspection media |
| User interface | React, TypeScript, Vite, Tailwind CSS, MapLibre GL, oidc-client-ts | The Workshop: explorer, map, live series, actions, approvals, audit |
| Packaging and tooling | uv workspace, pnpm, Ruff, pytest | One monorepo for services, packages and the UI |
| Deployment | Docker Compose (local), Helm (Kubernetes) | Local stack today; chart for the engine |
| Lakehouse | Apache Iceberg (PyIceberg, SQL catalog in PostgreSQL), Dagster | Versioned datasets, branches, sync and lineage |
| Continuous integration | GitHub Actions | Lint, tests, policy tests |

Planned for later phases, per the specification: dbt (Phase 2), CesiumJS 3D views (Phase 3), MLflow and simulation (Phase 4).

## Quick start

Requirements: Docker with Compose v2.24 or newer, `make`, `curl`. About 6 GB of free memory and a few spare CPU cores for the full stack; Kafka is slow to start on a machine that is already busy.

```bash
make up      # build and start everything; the first run pulls images
make seed    # apply the ontology packs, create demo drones and substations
make sim     # start publishing synthetic telemetry
```

Then try the slice. Dev users are `admin`, `operator`, `supervisor`, `approver`, `auditor` and `viewer`; each password is the user name followed by `-dev`. All but `approver` and `viewer` hold `marking:internal`, the clearance for data marked `internal` ([ADR 0010](docs/architecture/adr/0010-markings-on-reads.md)). A stack created before markings were enforced needs `make dev-markings` once.

```bash
API=http://localhost:8000
OPERATOR=$(make -s token AS=operator)
auth() { echo "Authorization: Bearer $1"; }

# An object, with its position and properties
curl -s -H "$(auth $OPERATOR)" $API/v1/objects/CommercialDrone/DRN-0003

# Its telemetry, straight from TimescaleDB (the simulator degrades this drone's actuators)
curl -s -H "$(auth $OPERATOR)" "$API/v1/objects/CommercialDrone/DRN-0003/timeseries/actuatorHealth?limit=5"

# actuatorHealth is marked `internal`. The viewer is not cleared for it, so it does not exist for them (404)
curl -s -H "$(auth $(make -s token AS=viewer))" "$API/v1/objects/CommercialDrone/DRN-0003/timeseries/actuatorHealth?limit=5"

# Follow a link, in either direction
curl -s -H "$(auth $OPERATOR)" $API/v1/objects/CommercialDrone/DRN-0001/links/inspects
curl -s -H "$(auth $OPERATOR)" $API/v1/objects/Substation/SS-HYD-07/links/inspectedBy

# A governed action: validated, policy-checked, applied, queued for write-back, audited
curl -s -X POST -H "$(auth $OPERATOR)" -H 'Content-Type: application/json' \
  -d '{"parameters": {"droneId": "DRN-0003", "reason": "actuator health trending down"}}' \
  $API/v1/actions/groundDrone/apply

# The same request as a viewer is denied by policy, and the denial is audited too
curl -s -X POST -H "$(auth $(make -s token AS=viewer))" -H 'Content-Type: application/json' \
  -d '{"parameters": {"droneId": "DRN-0002", "reason": "just testing"}}' \
  $API/v1/actions/groundDrone/apply

# Returning the drone to service needs a second person
EXEC=$(curl -s -X POST -H "$(auth $OPERATOR)" -H 'Content-Type: application/json' \
  -d '{"parameters": {"droneId": "DRN-0003"}}' $API/v1/actions/returnDroneToService/apply \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
curl -s -X POST -H "$(auth $(make -s token AS=approver))" $API/v1/action-executions/$EXEC/approve

# The audit chain, and proof it has not been altered
AUDITOR=$(make -s token AS=auditor)
curl -s -H "$(auth $AUDITOR)" "$API/v1/audit?limit=5"
curl -s -H "$(auth $AUDITOR)" $API/v1/audit/verify
```

After an action, `make outbox` shows its write-back go from `pending` to `delivered`. By default the executor's connectors only record the write-back in its log (`docker logs s2-action-executor`); [the executor's README](services/action-executor/README.md) shows how to point one at a real system. A stack created before Phase 2 needs `make executor-role` once, after `make up`.

The lakehouse: `make lakehouse-demo` writes the demo asset register to an Iceberg table and syncs it into the drones (serial numbers and firmware appear); [its README](services/pipeline-orchestrator/README.md) walks through a change on a branch, previewed, promoted and synced. `make pipelines-ui` starts Dagster at <http://localhost:3000>.

The Workshop UI is at <http://localhost:5173>: sign in as `operator` to browse the fleet, watch DRN-0003's actuators degrade, and ground it; as `approver` to decide the return to service. See [its README](apps/workshop-ui/README.md).

Interactive API documentation: <http://localhost:8000/docs>. Keycloak console: <http://localhost:8080> (`admin` / `admin_dev`).

`make help` lists every target. `make down` stops the stack; `make nuke` also deletes its data.

## Repository layout

```
services/
  ontology-engine/        FastAPI: registry, objects, actions, audit; Alembic migrations
  ingestion-connectors/   mqtt-bridge (MQTT -> Kafka), timeseries-sink (Kafka -> TimescaleDB)
  action-executor/        outbox relay; http and log connectors              (Phase 2, in progress)
  pipeline-orchestrator/  Iceberg datasets, branches, sync into objects, Dagster (Phase 2)
  ai-simulation/          stub: model bindings, what-if simulation, agents    (Phase 4)
packages/
  s2-common/              settings, OIDC token verification, OPA client, logging
apps/
  workshop-ui/            the Workshop: explorer, map, series, actions, approvals (Phase 3, in progress)
ontologies/               ontology-as-code packs (YAML): core-fleet, energy-grid
policies/                 OPA policies and their tests
tools/
  telemetry-sim/          synthetic UAV / UGV telemetry publisher
deploy/
  docker-compose/         the local stack and its support files
  helm/s2/                Kubernetes chart (ontology engine)
docs/
  architecture/           spec, overview, decision records (adr/)
```

## Development

Python 3.13 with [uv](https://docs.astral.sh/uv/) (it installs the interpreter if you lack it); Node 22+ with pnpm for the UI.

```bash
make sync         # create .venv with every workspace package, editable
make lint         # ruff check + format check
make test         # unit tests; database and policy tests skip unless configured
make test-stack   # every test, using the running Compose stack as the backend
make policy-test  # Rego unit tests (needs the opa binary)
make ui           # Workshop UI dev server on http://localhost:5173
```

The integration tests use a real PostgreSQL and a real OPA; how to point them at one is described at the top of [services/ontology-engine/tests/conftest.py](services/ontology-engine/tests/conftest.py). To change the ontology schema, edit the models and add an Alembic revision (see [services/ontology-engine/README.md](services/ontology-engine/README.md)). To add object types or actions, edit or add a pack under [ontologies/](ontologies/README.md).

## Roadmap

```mermaid
flowchart LR
    p1["<b>Phase 1</b><br/>Foundation and ontology kernel"]:::done
    p2["<b>Phase 2</b><br/>Lineage, actions and write-back"]:::done
    p3["<b>Phase 3</b><br/>Workshop UI and spatial twin"]:::now
    p4["<b>Phase 4</b><br/>Predictive AI, simulation, governed agents"]:::later
    p1 --> p2 --> p3 --> p4

    classDef done fill:#475569,stroke:#1e293b,color:#ffffff
    classDef now fill:#15803d,stroke:#14532d,color:#ffffff
    classDef next fill:#2563eb,stroke:#1e3a8a,color:#ffffff
    classDef later fill:#94a3b8,stroke:#475569,color:#0f172a
```

- **Phase 1 (done):** monorepo, local stack, ontology registry, telemetry path, governed actions, audit chain.
- **Phase 2 (done but dbt):** the action executor delivers queued write-backs (built, with Maximo and SAP PM adapters); the lakehouse with dataset branching and lineage (built; dbt and incremental sync to come); access control on reads (built).
- **Phase 3 (in progress):** the Workshop UI. Built: ontology explorer, objects with live series, map, actions, approval queue, audit log. Next: timeline view across objects, rule-driven triage queues, live updates instead of polling, the 3D view.
- **Phase 4:** predictive models bound to object types, what-if simulation, governed AI agents, external anchoring of the audit chain.

## Security notes for the local stack

The Compose stack is for development on one machine. It uses default passwords, no TLS, an anonymous MQTT broker, plaintext Kafka, a public Keycloak client that allows the password grant, and a superuser connection to the telemetry database, and an executor database role that bypasses row-level security (by design: it serves every tenant) with a default password. None of that is acceptable beyond localhost. Known gaps in the platform itself (markings not yet applied to write-back payloads or telemetry outside the API, no row-level security on telemetry, audit chain not yet anchored externally) are listed in the [overview](docs/architecture/overview.md#security-model-and-known-gaps).

## Licence

[Apache License 2.0](LICENSE).
