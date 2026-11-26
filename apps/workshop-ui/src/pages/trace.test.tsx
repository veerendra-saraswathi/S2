import { screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ActionExecution, AuditEntry, OntologyObject } from "../lib/types";
import { fakeApi, renderPage } from "../test/fixtures";
import { BuildPage } from "./BuildPage";
import { RequirementPage } from "./RequirementPage";

const obj = (object_type: string, primary_key: string, properties: Record<string, unknown>): OntologyObject => ({
  object_type,
  primary_key,
  properties,
  version: 1,
  created_at: "2026-11-26T15:35:00Z",
  updated_at: "2026-11-26T15:40:00Z",
});

const BUILD = obj("SoftwareBuild", "BLD-GW-e4eee545ac21", {
  name: "MAVLink gateway e4eee545ac21",
  target: "GROUND",
  status: "SIM_PASSED",
  gitCommit: "e4eee545ac2196253f25b9c16232c0d09d884afb",
  artifactDigest: "sha256:196b9c7a2877cc1c20afd80aa9cd25b70dabd28d6b74d025543e4cb1c8d1c520",
  builtAt: "2026-11-26T15:35:13Z",
});
const SITL = obj("TestRun", "TR-SITL-GW-1", {
  name: "ArduCopter SITL sortie through the gateway",
  kind: "SITL",
  status: "COMPLETED",
  verdict: "PASS",
  verdictNote: "rate: 10.04 Hz; p95 153 ms",
  startedAt: "2026-11-26T15:36:00Z",
  endedAt: "2026-11-26T15:39:00Z",
  logUri: "file:bin/pipeline/fly.log",
});
const UNIT = obj("TestRun", "TR-UNIT-GW-1", { name: "Gateway unit tests", kind: "UNIT", status: "COMPLETED", verdict: "PASS", startedAt: "2026-11-26T15:35:20Z" });
const REQ = obj("Requirement", "SW-TLM-001", {
  title: "Gateway telemetry rate and latency",
  statement: "The MAVLink gateway shall carry each vehicle's telemetry into S2 at 10 Hz.",
  status: "DRAFT",
  level: "SOFTWARE",
  verificationMethod: "TEST",
});
const ADVANCE: ActionExecution = {
  id: "e-1",
  action: "advanceBuild",
  action_type_version: 1,
  status: "succeeded",
  parameters: { buildId: BUILD.primary_key, stage: "SIM_PASSED", testRunId: SITL.primary_key, note: "passed" },
  actor_type: "user",
  actor_id: "operator",
  on_behalf_of: null,
  rationale: "Recorded from build record for BLD-GW-e4eee545ac21, pipeline run local/20261126T153513Z.",
  approved_by: null,
  policy_decision_id: null,
  error: null,
  created_at: "2026-11-26T15:40:00Z",
  completed_at: "2026-11-26T15:40:01Z",
  writebacks: [],
};

function buildApi(over: Record<string, unknown> = {}) {
  return fakeApi({
    object: vi.fn(async () => BUILD),
    linked: vi.fn(async (_type: string, _key: string, link: string) => (link === "testRuns" ? [SITL, UNIT] : link === "implements" ? [REQ] : [])),
    executions: vi.fn(async () => [ADVANCE]),
    audit: vi.fn(async () => []),
    ...over,
  });
}

describe("a build's gate page", () => {
  it("asks the engine for this build's executions only, and shows each step with its evidence", async () => {
    const api = buildApi();
    renderPage(<BuildPage />, { api, path: "/builds/:key", route: `/builds/${BUILD.primary_key}` });

    expect(await screen.findByRole("heading", { name: "MAVLink gateway e4eee545ac21" })).toBeInTheDocument();
    expect(api.executions).toHaveBeenCalledWith({ param: { buildId: BUILD.primary_key }, limit: 500 });

    const sim = screen.getByRole("listitem", { name: "Simulation passed" });
    expect(within(sim).getByText("reached")).toBeInTheDocument();
    expect(within(sim).getByText(/pipeline run local\/20261126T153513Z/)).toBeInTheDocument();
    const evidence = within(sim).getByLabelText("Evidence");
    expect(within(evidence).getByRole("link", { name: SITL.properties.name as string })).toHaveAttribute("href", `/objects/TestRun/${SITL.primary_key}`);
    expect(within(evidence).getByText("SITL")).toBeInTheDocument();
    expect(within(evidence).getByText("PASS")).toBeInTheDocument();
    expect(within(evidence).getByText("rate: 10.04 Hz; p95 153 ms")).toBeInTheDocument();

    expect(within(screen.getByRole("listitem", { name: "Bench passed" })).getByText(/Needs a passed HILS run/)).toBeInTheDocument();
    expect(screen.getByText(BUILD.properties.artifactDigest as string)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "SW-TLM-001" })).toHaveAttribute("href", "/requirements/SW-TLM-001");
    expect(screen.getByText("Test runs (2)")).toBeInTheDocument();
    expect(screen.queryByText("Refused by the policy")).not.toBeInTheDocument(); // viewers cannot read the audit log
    expect(api.audit).not.toHaveBeenCalled();
  });

  it("warns when the status went back behind the gate's record", async () => {
    renderPage(<BuildPage />, {
      api: buildApi({ object: vi.fn(async () => ({ ...BUILD, properties: { ...BUILD.properties, status: "BUILT" } })) }),
      path: "/builds/:key",
      route: `/builds/${BUILD.primary_key}`,
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(/stands at BUILT, behind the Simulation passed step/);
  });

  it("shows auditors the policy's refusals for this build", async () => {
    const entry: AuditEntry = {
      seq: 9,
      occurred_at: "2026-11-26T12:00:00Z",
      actor_type: "user",
      actor_id: "operator",
      on_behalf_of: null,
      event_type: "action.denied",
      resource_type: "action_type",
      resource_id: "advanceBuild",
      outcome: "denied",
      policy_decision_id: null,
      details: { parameters: { buildId: BUILD.primary_key, stage: "BENCH_PASSED" }, reasons: ["a SITL run does not earn BENCH_PASSED"] },
      redacted: false,
    };
    const api = buildApi({ audit: vi.fn(async () => [entry]) });
    renderPage(<BuildPage />, { api, path: "/builds/:key", route: `/builds/${BUILD.primary_key}`, roles: ["auditor"] });
    expect(await screen.findByText("a SITL run does not earn BENCH_PASSED")).toBeInTheDocument();
    expect(api.audit).toHaveBeenCalledWith({ event_type: "action.denied", limit: 500 });
  });
});

describe("a requirement's traceability page", () => {
  it("lists the builds that implement it and the runs that verify it, each with the build it tested", async () => {
    const linked = vi.fn(async (type: string, key: string, link: string) => {
      if (type === "Requirement" && link === "implementedBy") return [BUILD];
      if (type === "Requirement" && link === "verifiedBy") return [SITL, UNIT];
      if (type === "TestRun" && link === "testsBuild") return key === SITL.primary_key ? [BUILD] : [];
      return [];
    });
    renderPage(<RequirementPage />, {
      api: fakeApi({ object: vi.fn(async () => REQ), linked }),
      path: "/requirements/:key",
      route: "/requirements/SW-TLM-001",
    });

    expect(await screen.findByText("Gateway telemetry rate and latency")).toBeInTheDocument();
    expect(screen.getByText("DRAFT")).toBeInTheDocument();
    expect(screen.getByText(/2 verifying run\(s\); the latest passed on 1 of 1 build\(s\) tested/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "MAVLink gateway e4eee545ac21" })).toHaveAttribute("href", `/builds/${BUILD.primary_key}`);
    expect(screen.getByRole("link", { name: BUILD.primary_key })).toHaveAttribute("href", `/builds/${BUILD.primary_key}`);
    expect(screen.getByText("not linked to a build")).toBeInTheDocument(); // the unit run
    expect(linked).toHaveBeenCalledWith("TestRun", SITL.primary_key, "testsBuild");
  });
});
