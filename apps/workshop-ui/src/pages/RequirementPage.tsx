import { Link, useParams } from "react-router";

import { Badge, Card, Empty, ErrorBox, Loading, stamp, statusTone } from "../components/ui";
import { useAuth } from "../lib/auth";
import { verdictsByBuild } from "../lib/gate";
import type { OntologyObject } from "../lib/types";
import { useResource } from "../lib/useResource";

type VerifyingRun = { run: OntologyObject; build?: OntologyObject };

export function RequirementPage() {
  const { key = "" } = useParams();
  const { api } = useAuth();
  const requirement = useResource(`req:${key}`, () => api.object("Requirement", key));
  const builds = useResource(`req-builds:${key}`, () => api.linked("Requirement", key, "implementedBy"), 15_000);
  const runs = useResource(
    `req-runs:${key}`,
    async (): Promise<VerifyingRun[]> => {
      const found = await api.linked("Requirement", key, "verifiedBy");
      // Each run's build, so a verdict can be read against the build it tested.
      return Promise.all(
        found.map(async (run) => {
          const tested = await api.linked("TestRun", run.primary_key, "testsBuild");
          return { run, build: tested[0] };
        }),
      );
    },
    15_000,
  );
  const parents = useResource(`req-parents:${key}`, () => api.linked("Requirement", key, "derivedFrom"));

  const failure = requirement.error ?? builds.error ?? runs.error;
  if (failure) {
    return (
      <div className="space-y-4">
        <Breadcrumb title={key} />
        <ErrorBox error={failure} />
      </div>
    );
  }
  if (!requirement.data || !builds.data || !runs.data) return <Loading />;

  const p = requirement.data.properties;
  const latest = verdictsByBuild(runs.data);
  const passingBuilds = [...latest.values()].filter((r) => r.properties.verdict === "PASS").length;
  const sortedRuns = [...runs.data].sort((a, b) => String(b.run.properties.startedAt ?? "").localeCompare(String(a.run.properties.startedAt ?? "")));

  return (
    <div className="space-y-4">
      <Breadcrumb title={key} />
      <header className="space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-xl font-semibold">
            {key} <span className="font-normal text-stone-500">{String(p.title ?? "")}</span>
          </h1>
          {p.status ? <Badge tone={statusTone(p.status)}>{String(p.status)}</Badge> : null}
          {p.level ? <Badge>{String(p.level)}</Badge> : null}
        </div>
        {p.statement ? <p className="max-w-3xl text-sm">{String(p.statement)}</p> : null}
        <p className="text-sm text-stone-500">
          Verified by {String(p.verificationMethod ?? "an unstated method").toLowerCase()} ·{" "}
          {runs.data.length === 0
            ? "no run verifies it yet"
            : `${runs.data.length} verifying run(s); the latest passed on ${passingBuilds} of ${latest.size} build(s) tested`}
          {parents.data && parents.data.length > 0 && (
            <>
              {" "}
              · derived from{" "}
              {parents.data.map((parent, i) => (
                <span key={parent.primary_key}>
                  {i > 0 && ", "}
                  <Link to={`/requirements/${encodeURIComponent(parent.primary_key)}`} className="hover:underline">
                    {parent.primary_key}
                  </Link>
                </span>
              ))}
            </>
          )}
          {" · "}
          <Link to={`/objects/Requirement/${encodeURIComponent(key)}`} className="hover:underline">
            object
          </Link>
        </p>
      </header>

      <Card title={`Builds that implement it (${builds.data.length})`}>
        {builds.data.length === 0 ? (
          <Empty>No build claims to implement it.</Empty>
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-stone-500">
              <tr>
                <th className="py-1 font-normal">Build</th>
                <th className="py-1 font-normal">Gate</th>
                <th className="py-1 font-normal">Latest verifying run</th>
                <th className="py-1 font-normal">Built</th>
              </tr>
            </thead>
            <tbody>
              {[...builds.data]
                .sort((x, y) => String(y.properties.builtAt ?? "").localeCompare(String(x.properties.builtAt ?? "")))
                .map((b) => {
                const run = latest.get(b.primary_key);
                return (
                  <tr key={b.primary_key} className="border-t border-stone-100 align-top dark:border-stone-800">
                    <td className="py-1.5 pr-2">
                      <Link to={`/builds/${encodeURIComponent(b.primary_key)}`} className="font-medium hover:underline">
                        {String(b.properties.name ?? b.primary_key)}
                      </Link>
                      <p className="font-mono text-xs text-stone-500">{String(b.properties.gitCommit ?? "").slice(0, 12)}</p>
                    </td>
                    <td className="py-1.5 pr-2">
                      <Badge tone={b.properties.status === "REJECTED" ? "red" : "blue"}>{String(b.properties.status ?? "")}</Badge>
                    </td>
                    <td className="py-1.5 pr-2">
                      {run ? (
                        <>
                          <Badge tone={statusTone(run.properties.verdict)}>{String(run.properties.verdict ?? run.properties.status ?? "")}</Badge>{" "}
                          <span className="text-xs text-stone-500">{String(run.properties.kind ?? "")}</span>
                        </>
                      ) : (
                        <span className="text-xs text-stone-500">none verifies this requirement</span>
                      )}
                    </td>
                    <td className="py-1.5 text-xs text-stone-500">{b.properties.builtAt ? stamp(String(b.properties.builtAt)) : ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>

      <Card title={`Runs that verify it (${runs.data.length})`}>
        {sortedRuns.length === 0 ? (
          <Empty>No run verifies it yet.</Empty>
        ) : (
          <ul className="space-y-2 text-sm">
            {sortedRuns.map(({ run, build }) => (
              <li key={run.primary_key} className="border-t border-stone-100 pt-2 first:border-0 first:pt-0 dark:border-stone-800">
                <p className="flex flex-wrap items-center gap-2">
                  <Link to={`/objects/TestRun/${encodeURIComponent(run.primary_key)}`} className="font-medium hover:underline">
                    {String(run.properties.name ?? run.primary_key)}
                  </Link>
                  <Badge tone="blue">{String(run.properties.kind ?? "?")}</Badge>
                  <Badge tone={statusTone(run.properties.verdict ?? run.properties.status)}>{String(run.properties.verdict ?? run.properties.status ?? "")}</Badge>
                  <span className="text-xs text-stone-500">{run.properties.startedAt ? stamp(String(run.properties.startedAt)) : ""}</span>
                </p>
                <p className="text-xs text-stone-500">
                  {build ? (
                    <>
                      tested{" "}
                      <Link to={`/builds/${encodeURIComponent(build.primary_key)}`} className="hover:underline">
                        {build.primary_key}
                      </Link>
                    </>
                  ) : (
                    "not linked to a build"
                  )}
                </p>
                {run.properties.verdictNote ? <p className="mt-0.5 text-stone-600 dark:text-stone-400">{String(run.properties.verdictNote)}</p> : null}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

function Breadcrumb({ title }: { title: string }) {
  return (
    <nav className="text-sm text-stone-500">
      <Link to="/" className="hover:underline">
        Ontology
      </Link>{" "}
      /{" "}
      <Link to="/types/Requirement" className="hover:underline">
        Requirements
      </Link>{" "}
      / {title}
    </nav>
  );
}
