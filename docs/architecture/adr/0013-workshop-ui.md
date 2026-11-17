# 0013. The Workshop UI: a thin client of the public API, signed in with PKCE, MapLibre for the map

Status: accepted (November 2026). Phase 3, first slice.

## Context

Phase 3 turns the application shell into the operator workspace: browse the ontology, see the fleet on a map and its telemetry over time, act on objects, and decide approvals. Everything it needs already exists in the engine's API, behind roles, markings and policy.

## Decision

**Only the public API, through a same-origin proxy.** The UI calls `/api/...`, which the Vite server (development) or `vite preview` (the container) proxies to the engine. The browser talks to one origin, so the engine needs no CORS configuration. The UI decides nothing: it shows what the API returns, hides what the API hides (a marked property is simply absent; a marked type or series answers 404 and is shown as "not available to you"), and shows the policy's reasons when the API refuses. Role checks in the UI only decide which buttons to offer.

**Sign-in with Keycloak, authorization code with PKCE**, through `oidc-client-ts` and the realm's existing public client `s2-workshop`. Tokens are kept in session storage, so they do not outlive the tab. There is no silent renewal: when the 15-minute token expires, the user signs in again.

**MapLibre GL for the map**, as the specification names, with raster tiles from OpenStreetMap by default (`VITE_MAP_TILES` for any other XYZ source). Its worker is bundled by Vite and passed to MapLibre with `setWorkerUrl`: MapLibre 6 locates its module worker relative to its own file, which bundling moves, and without this the map draws no data. The map page is loaded on demand, so MapLibre (about 1 MB) is not in the main bundle.

**No state library, no chart library.** Data is fetched per view with a small hook that reloads on an interval (objects every 10 s, series every 5 s, the approval queue every 5 s). Time series are drawn as SVG paths. Both keep the bundle and the dependency list small; a cache (TanStack Query) and a chart library can replace them when a view needs more.

**One engine addition:** `GET /v1/action-executions` (newest first, filter by status and action), visible to anyone in the tenant like a single execution. `status=pending_approval` is the approval queue.

## Consequences

- Views: ontology explorer, objects of a type, one object (properties with their source columns, links, live series, the actions that target its type, its history for auditors), map, approvals, audit log with chain verification.
- Deep links work (`/objects/CommercialDrone/DRN-0003`), and sign-in returns to the page that asked for it.
- The container serves the production build through `vite preview`. That is adequate for the local stack. A deployment beyond it should serve the static files from a web server or CDN with the same `/api` proxy, or call the API cross-origin with CORS configured.
- Requests are polled, not pushed. Live updates (server-sent events or WebSockets from the engine) come with the timeline and triage views.
- OpenStreetMap's public tiles are for light use only; a deployment should use its own tile source.
- Not yet: the timeline view across objects, triage queues driven by rules, editing objects directly, and the 3D view (CesiumJS).
