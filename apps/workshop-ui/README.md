# Workshop UI

The operator workspace for S2. Today it is an application shell: a page that reports whether the ontology engine is ready. The composable workspace (ontology explorer, maps, telemetry timelines, action-driven triage queues) is Phase 3.

React, TypeScript, Vite and Tailwind CSS.

```bash
pnpm install                    # from the repository root
make ui                         # dev server on http://localhost:5173
pnpm --filter workshop-ui build # type-check and production build
```

The dev server proxies `/api` to the ontology engine (`http://localhost:8000`, or `S2_API_URL`), so the browser sees one origin and the API needs no CORS configuration.

Sign-in is not wired up yet. The Keycloak realm already has a public client for it, `s2-workshop` (authorization code with PKCE, redirect `http://localhost:5173/*`).
