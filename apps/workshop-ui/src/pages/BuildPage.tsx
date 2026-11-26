import { Link, useParams } from "react-router";

import { Badge, Card, Empty, ErrorBox, Loading, stamp, statusTone, who } from "../components/ui";
import { useAuth } from "../lib/auth";
import { EARNED_BY, journey, refusals, STAGE_LABEL, type Step } from "../lib/gate";
import type { ActionExecution, AuditEntry, OntologyObject } from "../lib/types";
import { type Resource, useResource } from "../lib/useResource";

/** How many recent policy refusals the page searches for this build's. */
const REFUSALS_SEARCHED = 500;

export function BuildPage() {
  const { key = "" } = useParams();
  const { api, hasRole, user } = useAuth();
  const me = user?.profile.sub;
  const build = useResource(`build:${key}`, () => api.object("SoftwareBuild", key), 15_000);
  const runs = useResource(`build-runs:${key}`, () => api.linked("SoftwareBuild", key, "testRuns"), 15_000);
  const requirements = useResource(`build-reqs:${key}`, () => api.linked("SoftwareBuild", key, "implements"));
  const executions = useResource(`build-gate:${key}`, () => api.executions({ param: { buildId: key }, limit: 500 }), 15_000);
  const auditor = hasRole("auditor", "ontology-admin");
  const denied = useResource(auditor ? `build-denied:${key}` : null, () => api.audit({ event_type: "action.denied", limit: REFUSALS_SEARCHED }));

  const failure = build.error ?? runs.error ?? executions.error;
  if (failure) {
    return (
      <div className="space-y-4">
        <Breadcrumb title={key} />
        <ErrorBox error={failure} />
      </div>
    );
  }
  if (!build.data || !runs.data || !executions.data) return <Loading />;

  const b = build.data;
  const j = journey(b, executions.data, runs.data);
  const p = b.properties;

  return (
    <div className="space-y-4">
      <Breadcrumb title={String(p.name ?? key)} />
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold">{String(p.name ?? key)}</h1>
          <p className="text-sm text-stone-500">
            Software build <span className="font-mono">{key}</span>
            {p.target ? <> · target {String(p.target)}</> : null} ·{" "}
            <Link to={`/objects/SoftwareBuild/${encodeURIComponent(key)}`} className="hover:underline">
              object and actions
            </Link>
          </p>
        </div>
        <Badge tone={gateTone(j.status)}>{j.status}</Badge>
      </header>

      {j.rejection && (
        <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200">
          Rejected {when(j.rejection)} by {who(j.rejection.actor_type, j.rejection.actor_id, me)}: {String(j.rejection.parameters.reason ?? "")}
        </div>
      )}
      {j.regressed && (
        <div role="alert" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
          The build stands at <strong>{j.regressed.status}</strong>, behind the {STAGE_LABEL[j.regressed.behind]} step recorded below. Its status was changed
          outside the gate, for instance by replacing the object; the gate's own history is unchanged.
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <Card title="Gate">
            <ol className="space-y-4" aria-label="Gate steps">
              {j.steps.map((step) => (
                <StepItem key={step.stage} step={step} build={b} me={me} />
              ))}
            </ol>
          </Card>
          <RunsCard runs={runs.data} />
          {auditor && <RefusalsCard state={denied} buildId={key} me={me} />}
        </div>
        <div className="space-y-4">
          <Card title="Artifact">
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-sm">
              <dt className="text-stone-500">Commit</dt>
              <dd className="break-all font-mono text-xs leading-5">{String(p.gitCommit ?? "not recorded")}</dd>
              <dt className="text-stone-500">Digest</dt>
              <dd className="break-all font-mono text-xs leading-5">{String(p.artifactDigest ?? "not recorded")}</dd>
              <dt className="text-stone-500">Built</dt>
              <dd>{p.builtAt ? stamp(String(p.builtAt)) : "not recorded"}</dd>
            </dl>
          </Card>
          <Card title="Implements">
            {requirements.error ? (
              <ErrorBox error={requirements.error} />
            ) : !requirements.data ? (
              <Loading />
            ) : requirements.data.length === 0 ? (
              <Empty>No requirement is linked.</Empty>
            ) : (
              <ul className="space-y-1.5 text-sm">
                {requirements.data.map((r) => (
                  <li key={r.primary_key}>
                    <Link to={`/requirements/${encodeURIComponent(r.primary_key)}`} className="font-medium hover:underline">
                      {r.primary_key}
                    </Link>{" "}
                    <span className="text-stone-500">{String(r.properties.title ?? "")}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}

function StepItem({ step, build, me }: { step: Step; build: OntologyObject; me?: string }) {
  const tone = step.problem ? "red" : step.state === "reached" ? "green" : step.state === "awaiting_approval" ? "amber" : "stone";
  const label = step.state === "reached" ? (step.problem ? "reached on unsound evidence" : "reached") : step.state === "awaiting_approval" ? "awaiting approval" : "not reached";
  return (
    <li className="border-l-2 border-stone-200 pl-3 dark:border-stone-700" aria-label={STAGE_LABEL[step.stage]}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{STAGE_LABEL[step.stage]}</span>
        <Badge tone={tone}>{label}</Badge>
      </div>
      {step.stage === "BUILT" ? (
        <p className="mt-1 text-sm text-stone-600 dark:text-stone-400">
          Recorded {build.properties.builtAt ? stamp(String(build.properties.builtAt)) : stamp(build.created_at)}, from commit{" "}
          <span className="font-mono text-xs">{String(build.properties.gitCommit ?? "?").slice(0, 12)}</span>.
        </p>
      ) : step.execution ? (
        <div className="mt-1 space-y-1 text-sm text-stone-600 dark:text-stone-400">
          <p>
            {step.state === "awaiting_approval" ? "Requested" : "Taken"} {when(step.execution)} by {who(step.execution.actor_type, step.execution.actor_id, me)}
            {step.execution.approved_by ? `, approved by ${who("user", step.execution.approved_by, me)}` : ""}.
          </p>
          {step.execution.rationale && <p className="italic">{step.execution.rationale}</p>}
          {typeof step.execution.parameters.justification === "string" && <p>Justification: {step.execution.parameters.justification}</p>}
          {step.problem && (
            <p role="alert" className="font-medium text-red-700 dark:text-red-400">
              The evidence does not support this step: {step.problem}.
            </p>
          )}
          {step.run && <Evidence run={step.run} stage={step.stage} />}
          {step.runMissing && (
            <p className="text-amber-700 dark:text-amber-400">
              It names run <span className="font-mono">{step.runMissing}</span>, which is not linked to this build.
            </p>
          )}
          {step.earlier.length > 0 && <p className="text-xs text-stone-500">Also taken {step.earlier.length} time(s) before, after the build was reset.</p>}
        </div>
      ) : (
        <p className="mt-1 text-sm text-stone-500">{waitingFor(step.stage)}</p>
      )}
      {step.refusedAtApproval.map((e) => (
        <p key={e.id} className="mt-1 text-sm text-red-700 dark:text-red-400">
          A clearance request was refused at approval {when(e)}
          {e.error?.message ? `: ${e.error.message}` : ""}.
        </p>
      ))}
    </li>
  );
}

function Evidence({ run, stage }: { run: OntologyObject; stage: Step["stage"] }) {
  const p = run.properties;
  const earns = EARNED_BY[stage];
  const kindOk = !earns || earns.includes(String(p.kind));
  return (
    <div className="rounded-md border border-stone-200 p-2 dark:border-stone-700" aria-label="Evidence">
      <p className="flex flex-wrap items-center gap-2">
        <Link to={`/objects/TestRun/${encodeURIComponent(run.primary_key)}`} className="font-medium text-stone-900 hover:underline dark:text-stone-100">
          {String(p.name ?? run.primary_key)}
        </Link>
        <Badge tone={kindOk ? "blue" : "red"}>{String(p.kind ?? "?")}</Badge>
        <Badge tone={statusTone(p.verdict)}>{String(p.verdict ?? "no verdict")}</Badge>
      </p>
      {p.verdictNote ? <p className="mt-1">{String(p.verdictNote)}</p> : null}
      <p className="mt-1 text-xs text-stone-500">
        {p.startedAt ? stamp(String(p.startedAt)) : "?"} to {p.endedAt ? stamp(String(p.endedAt)) : "?"}
        {p.logUri ? (
          <>
            {" "}
            · log <span className="font-mono">{String(p.logUri)}</span>
          </>
        ) : null}
      </p>
    </div>
  );
}

function RunsCard({ runs }: { runs: OntologyObject[] }) {
  const sorted = [...runs].sort((a, b) => String(b.properties.startedAt ?? "").localeCompare(String(a.properties.startedAt ?? "")));
  return (
    <Card title={`Test runs (${runs.length})`}>
      {sorted.length === 0 ? (
        <Empty>No run has tested this build.</Empty>
      ) : (
        <table className="w-full text-left text-sm">
          <thead className="text-xs text-stone-500">
            <tr>
              <th className="py-1 font-normal">Run</th>
              <th className="py-1 font-normal">Kind</th>
              <th className="py-1 font-normal">Verdict</th>
              <th className="py-1 font-normal">Started</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <tr key={r.primary_key} className="border-t border-stone-100 align-top dark:border-stone-800">
                <td className="py-1.5 pr-2">
                  <Link to={`/objects/TestRun/${encodeURIComponent(r.primary_key)}`} className="hover:underline">
                    {String(r.properties.name ?? r.primary_key)}
                  </Link>
                  {r.properties.verdictNote ? <p className="text-xs text-stone-500">{String(r.properties.verdictNote)}</p> : null}
                </td>
                <td className="py-1.5 pr-2">{String(r.properties.kind ?? "")}</td>
                <td className="py-1.5 pr-2">
                  <Badge tone={statusTone(r.properties.verdict ?? r.properties.status)}>{String(r.properties.verdict ?? r.properties.status ?? "")}</Badge>
                </td>
                <td className="py-1.5 text-xs text-stone-500">{r.properties.startedAt ? stamp(String(r.properties.startedAt)) : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function RefusalsCard({ state, buildId, me }: { state: Resource<AuditEntry[]>; buildId: string; me?: string }) {
  return (
    <Card title="Refused by the policy">
      {state.error ? (
        <ErrorBox error={state.error} />
      ) : !state.data ? (
        <Loading />
      ) : (
        (() => {
          const found = refusals(state.data, buildId);
          if (found.length === 0) return <Empty>No refusal among the latest {REFUSALS_SEARCHED} of any build.</Empty>;
          return (
            <ul className="space-y-2 text-sm">
              {found.map((r, i) => (
                <li key={`${r.at}-${i}`}>
                  <span className="font-medium">{refusedLabel(r.action, r.stage)}</span>{" "}
                  <span className="text-stone-500">
                    {stamp(r.at)} by {who("user", r.actor, me)}
                  </span>
                  <ul className="list-disc pl-5 text-red-700 dark:text-red-400">
                    {r.reasons.map((reason) => (
                      <li key={reason}>{reason}</li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          );
        })()
      )}
    </Card>
  );
}

function Breadcrumb({ title }: { title: string }) {
  return (
    <nav className="text-sm text-stone-500">
      <Link to="/" className="hover:underline">
        Ontology
      </Link>{" "}
      /{" "}
      <Link to="/types/SoftwareBuild" className="hover:underline">
        Software builds
      </Link>{" "}
      / {title}
    </nav>
  );
}

function refusedLabel(action: string, stage?: string): string {
  if (action === "clearBuildForFlight") return STAGE_LABEL.CLEARED_FOR_FLIGHT;
  if (action === "rejectBuild") return "Reject";
  return stage && stage in STAGE_LABEL ? STAGE_LABEL[stage as Step["stage"]] : action;
}

function gateTone(status: string) {
  if (status === "REJECTED") return "red" as const;
  if (status === "CLEARED_FOR_FLIGHT") return "green" as const;
  if (status === "BUILT") return "stone" as const;
  return "blue" as const;
}

function waitingFor(stage: Step["stage"]): string {
  if (stage === "CLEARED_FOR_FLIGHT") return "Needs a bench-passed build, a request, and a second person's approval.";
  return `Needs a passed ${(EARNED_BY[stage] ?? []).join(" or ")} run of this build.`;
}

function when(e: ActionExecution): string {
  return stamp(e.completed_at ?? e.created_at);
}
