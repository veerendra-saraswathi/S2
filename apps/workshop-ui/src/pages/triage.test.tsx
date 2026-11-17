import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { TriageQueue } from "../lib/types";
import { DRN3, DRONE, fakeApi, GROUND, renderPage } from "../test/fixtures";
import { describeEvidence, sortQueues, TriagePage } from "./TriagePage";

const rule = (api_name: string, severity: TriageQueue["rule"]["severity"], display_name = api_name) => ({
  api_name,
  display_name,
  description: null,
  object_type: "CommercialDrone",
  severity,
  conditions: [],
  actions: ["groundDrone"],
  version: 1,
});

const DEGRADING: TriageQueue = {
  rule: { ...rule("degradingActuators", "high", "Degrading actuators"), description: "Ground them before they fly again." },
  count: 1,
  warning: null,
  items: [
    {
      object: DRN3,
      evidence: [
        { property: "actuatorHealth", op: "lt", expected: 0.8, actual: 0.7234, observed_at: new Date(Date.now() - 12_000).toISOString() },
        { property: "status", op: "ne", expected: "GROUNDED", actual: "READY", observed_at: null },
      ],
    },
  ],
};
const LOW: TriageQueue = { rule: rule("lowBattery", "medium", "Low battery"), count: 0, warning: null, items: null };

describe("triage", () => {
  it("orders queues by severity, then by how full they are", () => {
    const queues = [
      { ...LOW, count: 5 },
      { ...LOW, rule: rule("a", "critical"), count: 0 },
      { ...LOW, rule: rule("b", "high"), count: 1 },
      { ...LOW, rule: rule("c", "high"), count: 3 },
    ];
    expect(sortQueues(queues).map((q) => q.rule.api_name)).toEqual(["a", "c", "b", "lowBattery"]);
  });

  it("says what the evidence is in words", () => {
    const actuator = { ...DRONE.properties[0], api_name: "actuatorHealth", display_name: "Actuator health", is_primary_key: false };
    const type = { ...DRONE, properties: [...DRONE.properties, actuator] };
    expect(describeEvidence(DEGRADING.items![0].evidence[0], type)).toBe("Actuator health 0.723 < 0.8");
    expect(describeEvidence(DEGRADING.items![0].evidence[1], DRONE)).toBe("Status READY ≠ GROUNDED");
  });

  it("opens the busiest urgent queue, shows why each object is there, and offers the rule's actions", async () => {
    const applyAction = vi.fn(async () => ({ data: { id: "e", writebacks: [] }, status: 200 }));
    const triageQueue = vi.fn(async () => DEGRADING);
    const api = fakeApi({
      triageQueues: vi.fn(async () => [LOW, DEGRADING]),
      triageQueue,
      objectType: vi.fn(async () => DRONE),
      actionTypes: vi.fn(async () => [GROUND]),
      applyAction,
    });
    renderPage(<TriagePage />, { api, path: "/triage", route: "/triage", roles: ["operator"] });
    const user = userEvent.setup();

    expect(await screen.findByRole("link", { name: "Garuda-3" })).toHaveAttribute("href", "/objects/CommercialDrone/DRN-0003");
    expect(triageQueue).toHaveBeenCalledWith("degradingActuators");
    expect(screen.getByText(/Actuator health 0.723 < 0.8/)).toBeInTheDocument();
    expect(screen.getByText(/12 s ago/)).toBeInTheDocument();
    expect(screen.getByText("Ground them before they fly again.")).toBeInTheDocument();

    const panel = screen.getByText("Ground drone").closest("div.rounded-lg") as HTMLElement;
    await user.click(within(panel).getByRole("button", { name: "Start" }));
    expect(within(panel).getByLabelText(/Drone Id/i)).toHaveValue("DRN-0003");
    await user.type(within(panel).getByLabelText(/Reason/), "triage: actuators");
    await user.click(within(panel).getByRole("button", { name: "Ground drone" }));
    expect(applyAction).toHaveBeenCalledWith("groundDrone", { droneId: "DRN-0003", reason: "triage: actuators" }, expect.any(String));
    expect(triageQueue.mock.calls.length).toBeGreaterThan(1); // the queue reloads after acting
  });

  it("switches queues", async () => {
    const triageQueue = vi.fn(async (name: string) => (name === "lowBattery" ? { ...LOW, items: [] } : DEGRADING));
    const api = fakeApi({ triageQueues: vi.fn(async () => [LOW, DEGRADING]), triageQueue, objectType: vi.fn(async () => DRONE), actionTypes: vi.fn(async () => []) });
    renderPage(<TriagePage />, { api, path: "/triage", route: "/triage" });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Low battery/ }));
    expect(await screen.findByText("Nothing in this queue right now.")).toBeInTheDocument();
    expect(triageQueue).toHaveBeenLastCalledWith("lowBattery");
  });
});
