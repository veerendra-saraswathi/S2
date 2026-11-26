import { describe, expect, it } from "vitest";

import { journey, refusals, verdictsByBuild } from "./gate";
import type { ActionExecution, AuditEntry, OntologyObject } from "./types";

const obj = (object_type: string, primary_key: string, properties: Record<string, unknown>): OntologyObject => ({
  object_type,
  primary_key,
  properties,
  version: 1,
  created_at: "2026-11-26T15:35:00Z",
  updated_at: "2026-11-26T15:40:00Z",
});

const exec = (action: string, parameters: Record<string, unknown>, over: Partial<ActionExecution> = {}): ActionExecution => ({
  id: `${action}-${String(parameters.stage ?? "")}-${over.created_at ?? "1"}`,
  action,
  action_type_version: 1,
  status: "succeeded",
  parameters,
  actor_type: "user",
  actor_id: "operator",
  on_behalf_of: null,
  approved_by: null,
  policy_decision_id: null,
  error: null,
  created_at: "2026-11-26T15:40:00Z",
  completed_at: "2026-11-26T15:40:01Z",
  writebacks: [],
  ...over,
});

const BUILD = "BLD-GW-e4eee545ac21";
const build = (status: string) => obj("SoftwareBuild", BUILD, { status, gitCommit: "e4eee545ac21", artifactDigest: "sha256:196b" });
const sitl = obj("TestRun", "TR-SITL-1", { kind: "SITL", verdict: "PASS", status: "COMPLETED" });

describe("a build's gate journey", () => {
  it("marks each step reached by the execution that took it, with the run it named", () => {
    const j = journey(build("SIM_PASSED"), [exec("advanceBuild", { buildId: BUILD, stage: "SIM_PASSED", testRunId: "TR-SITL-1" })], [sitl]);
    expect(j.steps.map((s) => [s.stage, s.state])).toEqual([
      ["BUILT", "reached"],
      ["SIM_PASSED", "reached"],
      ["BENCH_PASSED", "not_reached"],
      ["CLEARED_FOR_FLIGHT", "not_reached"],
    ]);
    expect(j.steps[1].run).toBe(sitl);
    expect(j.regressed).toBeUndefined();
  });

  it("ignores other builds' executions and other actions", () => {
    const j = journey(
      build("BUILT"),
      [
        exec("advanceBuild", { buildId: "BLD-OTHER", stage: "SIM_PASSED", testRunId: "TR-X" }),
        exec("recordTestVerdict", { buildId: BUILD, testRunId: "TR-SITL-1", verdict: "PASS" }),
      ],
      [sitl],
    );
    expect(j.steps.filter((s) => s.state === "reached").map((s) => s.stage)).toEqual(["BUILT"]);
  });

  it("shows a clearance request waiting for a second person, and one refused at approval", () => {
    const j = journey(build("BENCH_PASSED"), [
      exec("clearBuildForFlight", { buildId: BUILD, justification: "bench and SITL passed" }, { status: "pending_approval", created_at: "2026-11-27T10:00:00Z" }),
      exec("clearBuildForFlight", { buildId: BUILD, justification: "first try" }, { status: "rejected", created_at: "2026-11-26T10:00:00Z" }),
    ], []);
    const clear = j.steps[3];
    expect(clear.state).toBe("awaiting_approval");
    expect(clear.refusedAtApproval).toHaveLength(1);
  });

  it("says when the status was moved back behind a step the gate recorded", () => {
    const j = journey(build("BUILT"), [exec("advanceBuild", { buildId: BUILD, stage: "SIM_PASSED", testRunId: "TR-SITL-1" })], [sitl]);
    expect(j.regressed).toEqual({ status: "BUILT", behind: "SIM_PASSED" });
  });

  it("keeps the latest of several takes of one step, and flags a run that is not linked", () => {
    const j = journey(build("SIM_PASSED"), [
      exec("advanceBuild", { buildId: BUILD, stage: "SIM_PASSED", testRunId: "TR-OLD" }, { created_at: "2026-11-20T00:00:00Z" }),
      exec("advanceBuild", { buildId: BUILD, stage: "SIM_PASSED", testRunId: "TR-GONE" }, { created_at: "2026-11-26T00:00:00Z" }),
    ], [sitl]);
    expect(j.steps[1].execution?.parameters.testRunId).toBe("TR-GONE");
    expect(j.steps[1].earlier).toHaveLength(1);
    expect(j.steps[1].runMissing).toBe("TR-GONE");
  });

  it("reports a rejection", () => {
    const j = journey(build("REJECTED"), [exec("rejectBuild", { buildId: BUILD, reason: "wrong compiler" })], []);
    expect(j.rejection?.parameters.reason).toBe("wrong compiler");
  });
});

describe("evidence that does not support a step", () => {
  it("flags a reached step whose run failed, is of the wrong kind, or is not linked", () => {
    const hils = (verdict: string) => obj("TestRun", "TR-HILS-1", { kind: "HILS", verdict, status: "COMPLETED" });
    const bench = (run: OntologyObject[]) =>
      journey(build("BENCH_PASSED"), [exec("advanceBuild", { buildId: BUILD, stage: "BENCH_PASSED", testRunId: "TR-HILS-1" })], run).steps[2];
    expect(bench([hils("PASS")]).problem).toBeUndefined();
    expect(bench([hils("FAIL")]).problem).toBe("the run it names has not passed: it is COMPLETED with verdict FAIL");
    expect(bench([{ ...hils("PASS"), properties: { kind: "SITL", verdict: "PASS", status: "COMPLETED" } }]).problem).toMatch(/a SITL run does not earn/);
    expect(bench([]).problem).toMatch(/not linked to this build/);
  });
});

describe("refusals", () => {
  const denied = (resource_id: string, parameters: Record<string, unknown>, reasons: string[]): AuditEntry => ({
    seq: 1,
    occurred_at: "2026-11-26T12:00:00Z",
    actor_type: "user",
    actor_id: "operator",
    on_behalf_of: null,
    event_type: "action.denied",
    resource_type: "action_type",
    resource_id,
    outcome: "denied",
    policy_decision_id: null,
    details: { parameters, reasons, stage: "request" },
    redacted: false,
  });

  it("keeps this build's gate refusals with the policy's reasons", () => {
    const found = refusals(
      [
        denied("advanceBuild", { buildId: BUILD, stage: "BENCH_PASSED", testRunId: "TR-SITL-1" }, ["a SITL run does not earn BENCH_PASSED"]),
        denied("advanceBuild", { buildId: "BLD-OTHER", stage: "SIM_PASSED" }, ["other"]),
        denied("groundDrone", { buildId: BUILD }, ["not a gate action"]),
      ],
      BUILD,
    );
    expect(found).toEqual([
      { at: "2026-11-26T12:00:00Z", action: "advanceBuild", stage: "BENCH_PASSED", reasons: ["a SITL run does not earn BENCH_PASSED"], actor: "operator" },
    ]);
  });
});

describe("verdicts by build", () => {
  it("keeps the latest run per build", () => {
    const b1 = obj("SoftwareBuild", "B1", {});
    const old = obj("TestRun", "R1", { verdict: "FAIL", endedAt: "2026-11-20T00:00:00Z" });
    const recent = obj("TestRun", "R2", { verdict: "PASS", endedAt: "2026-11-26T00:00:00Z" });
    const latest = verdictsByBuild([{ run: recent, build: b1 }, { run: old, build: b1 }, { run: obj("TestRun", "R3", {}) }]);
    expect([...latest.keys()]).toEqual(["B1"]);
    expect(latest.get("B1")?.primary_key).toBe("R2");
  });
});
