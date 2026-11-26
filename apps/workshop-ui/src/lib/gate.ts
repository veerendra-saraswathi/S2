// A build's journey through the gate (drone-engineering pack: advanceBuild, clearBuildForFlight,
// rejectBuild), assembled from what the engine recorded. Pure, so the rules are tested apart
// from the page.

import type { ActionExecution, AuditEntry, OntologyObject } from "./types";

export const STAGES = ["BUILT", "SIM_PASSED", "BENCH_PASSED", "CLEARED_FOR_FLIGHT"] as const;
export type Stage = (typeof STAGES)[number];

export const STAGE_LABEL: Record<Stage, string> = {
  BUILT: "Built",
  SIM_PASSED: "Simulation passed",
  BENCH_PASSED: "Bench passed",
  CLEARED_FOR_FLIGHT: "Cleared for flight",
};

/** The kinds of run the policy accepts for each step (policies/s2/actions/build_gate.rego). */
export const EARNED_BY: Partial<Record<Stage, string[]>> = {
  SIM_PASSED: ["SITL", "SIMULATION"],
  BENCH_PASSED: ["HILS"],
};

export const GATE_ACTIONS = ["advanceBuild", "clearBuildForFlight", "rejectBuild"];

export type StepState = "reached" | "awaiting_approval" | "not_reached";

export type Step = {
  stage: Stage;
  state: StepState;
  /** The execution that took the step, or asked for it; the latest when there are several. */
  execution?: ActionExecution;
  /** Earlier executions for the same step, such as one taken again after a reset. */
  earlier: ActionExecution[];
  /** The run the step names, when the step needs one. */
  run?: OntologyObject;
  /** Named by the step but not among the runs linked to this build. */
  runMissing?: string;
  /** A clearance request someone refused at approval. */
  refusedAtApproval: ActionExecution[];
  /** Why the run a reached step names does not support it, when it does not. */
  problem?: string;
};

export type Journey = {
  steps: Step[];
  status: string;
  /** Set when the build's status sits behind a step the gate recorded: it was changed outside the gate. */
  regressed?: { status: string; behind: Stage };
  rejection?: ActionExecution;
};

const rank = (status: string) => STAGES.indexOf(status as Stage);

export function journey(build: OntologyObject, executions: ActionExecution[], runs: OntologyObject[]): Journey {
  const status = String(build.properties.status ?? "BUILT");
  const runById = new Map(runs.map((r) => [r.primary_key, r]));
  const mine = executions
    .filter((e) => GATE_ACTIONS.includes(e.action) && e.parameters.buildId === build.primary_key)
    .sort((a, b) => b.created_at.localeCompare(a.created_at)); // newest first

  const steps: Step[] = STAGES.map((stage) => {
    if (stage === "BUILT") return { stage, state: "reached", earlier: [], refusedAtApproval: [] };
    const asks =
      stage === "CLEARED_FOR_FLIGHT"
        ? mine.filter((e) => e.action === "clearBuildForFlight")
        : mine.filter((e) => e.action === "advanceBuild" && e.parameters.stage === stage);
    const done = asks.filter((e) => e.status === "succeeded");
    const pending = asks.find((e) => e.status === "pending_approval");
    const execution = done[0] ?? pending;
    const step: Step = {
      stage,
      state: done.length ? "reached" : pending ? "awaiting_approval" : "not_reached",
      execution,
      earlier: done.slice(1),
      refusedAtApproval: asks.filter((e) => e.status === "rejected"),
    };
    const runId = execution?.parameters.testRunId;
    if (typeof runId === "string") {
      step.run = runById.get(runId);
      if (!step.run) step.runMissing = runId;
    }
    if (step.state === "reached") step.problem = evidenceProblem(step);
    return step;
  });

  const result: Journey = { steps, status };
  const furthest = [...steps].reverse().find((s) => s.state === "reached");
  if (furthest && rank(status) >= 0 && rank(status) < rank(furthest.stage)) {
    result.regressed = { status, behind: furthest.stage };
  }
  result.rejection = mine.find((e) => e.action === "rejectBuild" && e.status === "succeeded");
  return result;
}

/**
 * What is wrong with a reached step's evidence, judged by today's state of the run: the
 * rules the policy applies now. A step taken before the policy checked runs, or whose run
 * was re-recorded since, can fail them.
 */
export function evidenceProblem(step: Step): string | undefined {
  const earns = EARNED_BY[step.stage];
  if (!earns) return undefined;
  if (step.runMissing) return `the run it names, ${step.runMissing}, is not linked to this build`;
  if (!step.run) return "it names no run";
  const p = step.run.properties;
  if (!earns.includes(String(p.kind))) return `a ${String(p.kind)} run does not earn this step; it takes ${earns.join(" or ")}`;
  if (p.status !== "COMPLETED" || p.verdict !== "PASS") return `the run it names has not passed: it is ${String(p.status ?? "?")} with verdict ${String(p.verdict ?? "none")}`;
  return undefined;
}

/** Gate requests the policy refused for this build, from the audit log (auditors only). */
export function refusals(entries: AuditEntry[], buildId: string): { at: string; action: string; stage?: string; reasons: string[]; actor: string }[] {
  return entries
    .filter((e) => e.event_type === "action.denied" && GATE_ACTIONS.includes(String(e.resource_id)))
    .filter((e) => (e.details.parameters as Record<string, unknown> | undefined)?.buildId === buildId)
    .map((e) => {
      const parameters = e.details.parameters as Record<string, unknown>;
      const reasons = Array.isArray(e.details.reasons) ? e.details.reasons.map(String) : [];
      return {
        at: e.occurred_at,
        action: String(e.resource_id),
        stage: typeof parameters.stage === "string" ? parameters.stage : undefined,
        reasons,
        actor: e.actor_id,
      };
    });
}

/** The latest verdict of the runs that verify a requirement, per build they tested. */
export function verdictsByBuild(runs: { run: OntologyObject; build?: OntologyObject }[]): Map<string, OntologyObject> {
  const latest = new Map<string, OntologyObject>();
  for (const { run, build } of runs) {
    if (!build) continue;
    const seen = latest.get(build.primary_key);
    const when = String(run.properties.endedAt ?? run.properties.startedAt ?? "");
    const seenWhen = String(seen?.properties.endedAt ?? seen?.properties.startedAt ?? "");
    if (!seen || when > seenWhen) latest.set(build.primary_key, run);
  }
  return latest;
}
