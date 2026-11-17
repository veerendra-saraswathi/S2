import { lazy, Suspense } from "react";
import { createBrowserRouter, NavLink, Outlet, RouterProvider } from "react-router";

import { Button, ErrorBox, Loading } from "./components/ui";
import { AuthProvider, CALLBACK_PATH, useAuth } from "./lib/auth";
import { useResource } from "./lib/useResource";
import { ApprovalsPage } from "./pages/ApprovalsPage";
import { AuditPage } from "./pages/AuditPage";
import { CallbackPage } from "./pages/CallbackPage";
import { ExplorerPage } from "./pages/ExplorerPage";
import { ObjectPage } from "./pages/ObjectPage";
import { TriagePage } from "./pages/TriagePage";
import { TypePage } from "./pages/TypePage";

// MapLibre is most of the bundle: load it only when the map is opened.
const MapPage = lazy(() => import("./pages/MapPage").then((m) => ({ default: m.MapPage })));

type Readiness = { status: "ready" | "not_ready"; checks: Record<string, boolean> };

function EngineStatus() {
  const readiness = useResource(
    "readyz",
    async () => {
      const response = await fetch("/api/readyz");
      return (await response.json()) as Readiness;
    },
    10_000,
  );
  const ok = readiness.data?.status === "ready";
  const label = readiness.error ? "engine unreachable" : ok ? "engine ready" : "engine not ready";
  return (
    <span className="flex items-center gap-1.5 text-xs text-stone-500" title={label}>
      <span aria-hidden="true" className={`size-2 rounded-full ${readiness.data || readiness.error ? (ok ? "bg-emerald-500" : "bg-red-500") : "bg-stone-300"}`} />
      <span className="hidden sm:inline">{label}</span>
    </span>
  );
}

/** Objects waiting across the urgent queues (critical and high), polled. */
function TriageCount() {
  const { api } = useAuth();
  const queues = useResource("triage:count", () => api.triageQueues(), 15_000);
  const urgent = (queues.data ?? []).filter((q) => q.rule.severity === "critical" || q.rule.severity === "high").reduce((n, q) => n + q.count, 0);
  if (!urgent) return null;
  return (
    <span className="ml-1 rounded-full bg-red-600 px-1.5 text-xs font-semibold text-white" aria-label={`${urgent} urgent`}>
      {urgent}
    </span>
  );
}

function Layout() {
  const { user, ready, username, roles, login, logout, hasRole } = useAuth();
  const link = ({ isActive }: { isActive: boolean }) =>
    `rounded-md px-2.5 py-1 text-sm ${isActive ? "bg-stone-200 font-medium dark:bg-stone-800" : "text-stone-600 hover:text-stone-900 dark:text-stone-400 dark:hover:text-stone-100"}`;

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 border-b border-stone-200 bg-stone-50/90 backdrop-blur dark:border-stone-800 dark:bg-stone-950/90">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5">
          <span className="font-semibold">
            S2 <span className="font-normal text-stone-500">Workshop</span>
          </span>
          {user && (
            <nav className="flex flex-wrap gap-1" aria-label="Main">
              <NavLink to="/" end className={link}>
                Ontology
              </NavLink>
              <NavLink to="/triage" className={link}>
                Triage <TriageCount />
              </NavLink>
              <NavLink to="/map" className={link}>
                Map
              </NavLink>
              <NavLink to="/approvals" className={link}>
                Approvals
              </NavLink>
              {hasRole("auditor", "ontology-admin") && (
                <NavLink to="/audit" className={link}>
                  Audit
                </NavLink>
              )}
            </nav>
          )}
          <div className="ml-auto flex items-center gap-3">
            <EngineStatus />
            {user && (
              <>
                <span className="text-sm" title={roles.join(", ")}>
                  {username}
                </span>
                <Button kind="secondary" onClick={() => void logout()}>
                  Sign out
                </Button>
              </>
            )}
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        {!ready ? (
          <Loading />
        ) : user ? (
          <Outlet />
        ) : (
          <SignIn onSignIn={() => void login()} />
        )}
      </main>
    </div>
  );
}

function SignIn({ onSignIn }: { onSignIn: () => void }) {
  return (
    <div className="mx-auto max-w-md space-y-4 py-12 text-center">
      <h1 className="text-2xl font-semibold">Sign in to the Workshop</h1>
      <p className="text-sm text-stone-500">
        You sign in with the S2 identity provider. On the local stack the dev users are <code className="font-mono">operator</code>,{" "}
        <code className="font-mono">supervisor</code>, <code className="font-mono">approver</code>, <code className="font-mono">auditor</code> and{" "}
        <code className="font-mono">viewer</code>; each password is the name followed by <code className="font-mono">-dev</code>.
      </p>
      <Button onClick={onSignIn}>Sign in</Button>
    </div>
  );
}

function NotFound() {
  return <ErrorBox error={new Error("There is no page here.")} />;
}

export const routes = [
  { path: CALLBACK_PATH, element: <CallbackPage /> },
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, element: <ExplorerPage /> },
      { path: "types/:type", element: <TypePage /> },
      { path: "objects/:type/:key", element: <ObjectPage /> },
      {
        path: "map",
        element: (
          <Suspense fallback={<Loading label="Loading the map…" />}>
            <MapPage />
          </Suspense>
        ),
      },
      { path: "approvals", element: <ApprovalsPage /> },
      { path: "triage", element: <TriagePage /> },
      { path: "audit", element: <AuditPage /> },
      { path: "*", element: <NotFound /> },
    ],
  },
];

const router = createBrowserRouter(routes);

export function App() {
  return (
    <AuthProvider>
      <RouterProvider router={router} />
    </AuthProvider>
  );
}
