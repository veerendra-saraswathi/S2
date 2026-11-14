import { useEffect, useState } from "react";

type Readiness = {
  status: "ready" | "not_ready";
  checks: Record<string, boolean>;
};

type ApiState =
  | { kind: "loading" }
  | { kind: "unreachable" }
  | { kind: "ok"; readiness: Readiness };

const CHECK_LABELS: Record<string, string> = {
  database: "Ontology database",
  policy_engine: "Policy engine",
};

function useReadiness(pollMs = 5000): ApiState {
  const [state, setState] = useState<ApiState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;

    async function check() {
      try {
        // /readyz answers 200 or 503; both carry the same JSON body.
        const response = await fetch("/api/readyz");
        const readiness = (await response.json()) as Readiness;
        if (!cancelled) setState({ kind: "ok", readiness });
      } catch {
        if (!cancelled) setState({ kind: "unreachable" });
      }
    }

    void check();
    const timer = setInterval(check, pollMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [pollMs]);

  return state;
}

function Dot({ ok }: { ok: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={`inline-block size-2.5 rounded-full ${ok ? "bg-emerald-500" : "bg-red-500"}`}
    />
  );
}

function StatusCard({ state }: { state: ApiState }) {
  if (state.kind === "loading") {
    return <p className="text-sm text-stone-500">Checking the ontology engine…</p>;
  }
  if (state.kind === "unreachable") {
    return (
      <div className="space-y-1 text-sm">
        <p className="flex items-center gap-2 font-medium">
          <Dot ok={false} /> Ontology engine unreachable
        </p>
        <p className="text-stone-500">
          Start the stack with <code className="font-mono">make up</code>, then this page updates by itself.
        </p>
      </div>
    );
  }
  const { readiness } = state;
  return (
    <ul className="space-y-2 text-sm">
      <li className="flex items-center gap-2 font-medium">
        <Dot ok={readiness.status === "ready"} />
        Ontology engine {readiness.status === "ready" ? "ready" : "not ready"}
      </li>
      {Object.entries(readiness.checks).map(([name, ok]) => (
        <li key={name} className="flex items-center gap-2 pl-4 text-stone-600 dark:text-stone-400">
          <Dot ok={ok} />
          {CHECK_LABELS[name] ?? name}
        </li>
      ))}
    </ul>
  );
}

export function App() {
  const state = useReadiness();

  return (
    <main className="mx-auto max-w-2xl space-y-8 px-6 py-16">
      <header className="space-y-2">
        <p className="text-sm font-medium uppercase tracking-wide text-stone-500">Samanvaya 2</p>
        <h1 className="text-3xl font-semibold">Workshop</h1>
        <p className="text-stone-600 dark:text-stone-400">
          The operator workspace for the S2 digital twin. This is the application shell: maps, telemetry
          timelines and action-driven triage queues arrive in Phase 3.
        </p>
      </header>

      <section
        aria-live="polite"
        className="rounded-xl border border-stone-200 bg-white p-5 dark:border-stone-800 dark:bg-stone-900"
      >
        <StatusCard state={state} />
      </section>

      <section className="space-y-2 text-sm">
        <h2 className="font-medium">Until then</h2>
        <ul className="list-disc space-y-1 pl-5 text-stone-600 dark:text-stone-400">
          <li>
            Explore and call the API in the{" "}
            <a className="underline" href="http://localhost:8000/docs">
              interactive API docs
            </a>
            .
          </li>
          <li>
            Manage dev users and roles in the{" "}
            <a className="underline" href="http://localhost:8080">
              Keycloak console
            </a>
            .
          </li>
        </ul>
      </section>
    </main>
  );
}
