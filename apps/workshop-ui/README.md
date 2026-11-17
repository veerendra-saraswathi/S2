# Workshop UI

The operator workspace for S2: browse the ontology, see the fleet on a map and its telemetry live, run governed actions, decide approvals, read the audit log. Design: [ADR 0013](../../docs/architecture/adr/0013-workshop-ui.md).

React, TypeScript, Vite, Tailwind CSS, React Router, `oidc-client-ts` (sign-in), MapLibre GL (map).

## Run it

With the Compose stack (`make up`) it runs as the `workshop-ui` container: open <http://localhost:5173> and sign in with a dev user (`operator`, `supervisor`, `approver`, `auditor`, `viewer`; password is the name followed by `-dev`).

For hot reload while working on it, stop the container and run the dev server instead (needs Node 22 and pnpm):

```bash
docker stop s2-workshop-ui
pnpm install          # from the repository root
make ui               # http://localhost:5173
make ui-test          # Vitest + Testing Library
pnpm --filter workshop-ui build
```

Both proxy `/api` to the ontology engine (`S2_API_URL`, default `http://localhost:8000`), so the browser sees one origin.

## What is where

| View | Path | Shows |
|---|---|---|
| Ontology | `/` | object types, with their markings, series, position and backing dataset |
| A type | `/types/:type` | its objects, paged |
| An object | `/objects/:type/:key` | properties (and the dataset column each came from), live series, links, the actions for its type, its history (auditors) |
| Map | `/map` | every object with a position, coloured by type; grounded drones ringed red; click to open |
| Approvals | `/approvals` | the queue of actions awaiting a second person; approve or reject (not your own) |
| Audit | `/audit` | entries with provenance and redaction marked, and the chain's verification (auditors, admins) |

The UI shows what the API allows: a property you are not cleared for is absent, a marked series says "not available to you", and a refused action shows the policy's reasons.

## Configuration (build time)

| Variable | Default |
|---|---|
| `VITE_OIDC_AUTHORITY` | `http://localhost:8080/realms/s2` |
| `VITE_OIDC_CLIENT_ID` | `s2-workshop` |
| `VITE_MAP_TILES` | `https://tile.openstreetmap.org/{z}/{x}/{y}.png` |
| `VITE_MAP_ATTRIBUTION` | `© OpenStreetMap contributors` |
