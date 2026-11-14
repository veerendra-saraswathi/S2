# Samanvaya 2 (S2)

An open-source, cloud-native **enterprise data operating system and operational digital twin** for civilian commercial industries: utility grid inspection, smart manufacturing and warehouse logistics, civil infrastructure monitoring, precision agriculture.

Samanvaya 0.5 and 1 run the fleet: field telemetry and edge autonomy for commercial UAVs and UGVs. S2 sits above them and turns that telemetry, together with ERP / CMMS / SCADA / GIS records, into a typed **ontology** of objects, links and governed actions that people and software can query, act on and audit.

The product specification is in [docs/architecture/spec.md](docs/architecture/spec.md). What is built, and how, is in [docs/architecture/overview.md](docs/architecture/overview.md).

## Status

Phase 1 of 4 (foundation and ontology kernel). One vertical slice works end to end:

| Area | State |
|---|---|
| Ontology engine: registry, objects, links, governed actions, audit chain | Implemented, tested |
| Ingestion: MQTT bridge, Kafka log, TimescaleDB sink, telemetry simulator | Implemented, tested |
| Policies (OPA / Rego) for actions, with four-eyes approval | Implemented, tested |
| Local stack (Docker Compose) | Written; see the first-boot checklist |
| Helm chart (ontology engine only) | Written, not yet linted or installed |
| Workshop UI | Application shell only (Phase 3) |
| Action executor (write-back delivery), pipeline orchestrator, AI and simulation | Stubs (Phases 2 and 4) |

The scaffold was generated in an environment without Docker. What was and was not run is listed in [docs/scaffold-verification.md](docs/scaffold-verification.md); read it before the first `make up`.

## Quick start

Requirements: Docker with Compose v2.24 or newer, `make`, `curl`. About 6 GB of free memory for the full stack.

```bash
make up      # build and start everything; the first run pulls images
make seed    # apply the ontology packs, create demo drones and substations
make sim     # start publishing synthetic telemetry
```

Then try the slice. Dev users are `admin`, `operator`, `supervisor`, `approver`, `auditor` and `viewer`; each password is the user name followed by `-dev`.

```bash
API=http://localhost:8000
OPERATOR=$(make -s token AS=operator)
auth() { echo "Authorization: Bearer $1"; }

# An object, with its position and properties
curl -s -H "$(auth $OPERATOR)" $API/v1/objects/CommercialDrone/DRN-0003

# Its telemetry, straight from TimescaleDB (the simulator degrades this drone's actuators)
curl -s -H "$(auth $OPERATOR)" "$API/v1/objects/CommercialDrone/DRN-0003/timeseries/actuatorHealth?limit=5"

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

Interactive API documentation: <http://localhost:8000/docs>. Keycloak console: <http://localhost:8080> (`admin` / `admin_dev`).

`make help` lists every target. `make down` stops the stack; `make nuke` also deletes its data.

## Repository layout

```
services/
  ontology-engine/        FastAPI: registry, objects, actions, audit; Alembic migrations
  ingestion-connectors/   mqtt-bridge (MQTT -> Kafka), timeseries-sink (Kafka -> TimescaleDB)
  action-executor/        stub: outbox relay and ERP / CMMS write-back        (Phase 2)
  pipeline-orchestrator/  stub: Dagster + dbt over the Iceberg lakehouse      (Phase 2)
  ai-simulation/          stub: model bindings, what-if simulation, agents    (Phase 4)
packages/
  s2-common/              settings, OIDC token verification, OPA client, logging
apps/
  workshop-ui/            React + TypeScript + Tailwind shell                 (Phase 3)
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

## Security notes for the local stack

The Compose stack is for development on one machine. It uses default passwords, no TLS, an anonymous MQTT broker, plaintext Kafka, a public Keycloak client that allows the password grant, and a superuser connection to the telemetry database. None of that is acceptable beyond localhost. Known gaps in the platform itself (property markings recorded but not yet enforced on reads, no row-level security on telemetry, audit chain not yet anchored externally) are listed in the [overview](docs/architecture/overview.md#security-model-and-known-gaps).

## License

[Apache License 2.0](LICENSE).
