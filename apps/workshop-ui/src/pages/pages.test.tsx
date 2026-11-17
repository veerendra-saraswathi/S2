import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../lib/api";
import type { ActionExecution } from "../lib/types";
import { DRN3, DRONE, fakeApi, GROUND, INSPECTS, renderPage, RETURN } from "../test/fixtures";
import { ApprovalsPage } from "./ApprovalsPage";
import { ExplorerPage } from "./ExplorerPage";
import { ObjectPage } from "./ObjectPage";
import { TypePage } from "./TypePage";

const execution = (over: Partial<ActionExecution> = {}): ActionExecution => ({
  id: "e-1",
  action: "returnDroneToService",
  action_type_version: 1,
  status: "pending_approval",
  parameters: { droneId: "DRN-0003" },
  actor_type: "user",
  actor_id: "operator-sub-123456",
  on_behalf_of: null,
  approved_by: null,
  policy_decision_id: null,
  error: null,
  created_at: new Date().toISOString(),
  completed_at: null,
  writebacks: [],
  ...over,
});

function objectApi(over: Record<string, unknown> = {}) {
  return fakeApi({
    objectType: vi.fn(async () => DRONE),
    object: vi.fn(async () => DRN3),
    linkTypes: vi.fn(async () => [INSPECTS]),
    actionTypes: vi.fn(async () => [GROUND, RETURN]),
    linked: vi.fn(async () => [{ ...DRN3, object_type: "Substation", primary_key: "SS-HYD-07", properties: { name: "Gachibowli 220 kV" } }]),
    timeseries: vi.fn(async () => {
      throw new ApiError(404, "CommercialDrone has no time-series property 'actuatorHealth'");
    }),
    audit: vi.fn(async () => []),
    ...over,
  });
}

describe("an object", () => {
  it("shows its properties, links and actions; a marked series it cannot read is 'not available'", async () => {
    renderPage(<ObjectPage />, { api: objectApi(), path: "/objects/:type/:key", route: "/objects/CommercialDrone/DRN-0003" });

    expect(await screen.findByRole("heading", { name: "Garuda-3" })).toBeInTheDocument();
    expect(screen.getByText("4.3.0")).toBeInTheDocument();
    expect(screen.getByText("from firmware_version")).toBeInTheDocument();
    expect(screen.getByText("17.4239, 78.3483")).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "Gachibowli 220 kV" })).toHaveAttribute("href", "/objects/Substation/SS-HYD-07");
    expect(await screen.findByText("Not available to you.")).toBeInTheDocument();
    expect(screen.getByText("Ground drone")).toBeInTheDocument();
    expect(screen.queryByText("History")).not.toBeInTheDocument(); // viewers do not see the audit card
  });

  it("runs an action with the object filled in, and says when it awaits approval", async () => {
    const applyAction = vi.fn(async () => ({ data: execution(), status: 202 }));
    const api = objectApi({ applyAction });
    renderPage(<ObjectPage />, { api, path: "/objects/:type/:key", route: "/objects/CommercialDrone/DRN-0003", roles: ["operator"] });
    const user = userEvent.setup();

    const panel = (await screen.findByText("Return drone to service")).closest("div.rounded-lg") as HTMLElement;
    await user.click(within(panel).getByRole("button", { name: "Start" }));
    expect(within(panel).getByLabelText(/Drone Id/i)).toHaveValue("DRN-0003");
    expect(within(panel).getByLabelText(/Drone Id/i)).toBeDisabled();
    await user.type(within(panel).getByLabelText("Note"), "inspected");
    await user.click(within(panel).getByRole("button", { name: "Request" }));

    expect(applyAction).toHaveBeenCalledWith("returnDroneToService", { droneId: "DRN-0003", note: "inspected" }, expect.any(String));
    expect(await within(panel).findByText("awaiting approval")).toBeInTheDocument();
  });

  it("shows why a policy refused, and validates before sending", async () => {
    const applyAction = vi.fn(async () => {
      throw new ApiError(403, "denied by policy s2/actions/ground_drone", { reasons: ["only operators may ground a drone"] });
    });
    renderPage(<ObjectPage />, { api: objectApi({ applyAction }), path: "/objects/:type/:key", route: "/objects/CommercialDrone/DRN-0003" });
    const user = userEvent.setup();
    const panel = (await screen.findByText("Ground drone")).closest("div.rounded-lg") as HTMLElement;
    await user.click(within(panel).getByRole("button", { name: "Start" }));

    await user.click(within(panel).getByRole("button", { name: "Ground drone" }));
    expect(within(panel).getByText("Reason: required")).toBeInTheDocument();
    expect(applyAction).not.toHaveBeenCalled();

    await user.type(within(panel).getByLabelText(/Reason/), "vibration");
    await user.click(within(panel).getByRole("button", { name: "Ground drone" }));
    const alert = await within(panel).findByRole("alert");
    expect(alert).toHaveTextContent("Not allowed: denied by policy");
    expect(alert).toHaveTextContent("only operators may ground a drone");
  });

  it("an object the caller cannot see is an error, not a blank page", async () => {
    const api = objectApi({ object: vi.fn(async () => Promise.reject(new ApiError(404, "object type 'Vault' does not exist"))) });
    renderPage(<ObjectPage />, { api, path: "/objects/:type/:key", route: "/objects/Vault/V1" });
    expect(await screen.findByRole("alert")).toHaveTextContent("Not found: object type 'Vault' does not exist");
  });

  it("auditors see the history, with where values came from", async () => {
    const audit = vi.fn(async () => [
      {
        seq: 54,
        occurred_at: new Date().toISOString(),
        actor_type: "service",
        actor_id: "272fbeaf-4bd9",
        on_behalf_of: null,
        event_type: "object.modified",
        resource_type: "CommercialDrone",
        resource_id: "DRN-0003",
        outcome: "success",
        policy_decision_id: null,
        details: { provenance: { dataset: "assets.commercial_drone", branch: "main" } },
        redacted: false,
      },
    ]);
    renderPage(<ObjectPage />, { api: objectApi({ audit }), path: "/objects/:type/:key", route: "/objects/CommercialDrone/DRN-0003", roles: ["auditor"] });
    expect(await screen.findByText(/by service 272fbeaf · from assets.commercial_drone@main/)).toBeInTheDocument();
    expect(audit).toHaveBeenCalledWith({ resource_type: "CommercialDrone", resource_id: "DRN-0003", limit: 20 });
  });
});

describe("the approval queue", () => {
  it("an approver approves someone else's request", async () => {
    const approve = vi.fn(async () => ({ data: execution({ status: "succeeded" }), status: 200 }));
    const api = fakeApi({ executions: vi.fn(async () => [execution()]), approve });
    renderPage(<ApprovalsPage />, { api, path: "/approvals", route: "/approvals", roles: ["approver"], sub: "approver-sub" });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Approve" }));
    expect(approve).toHaveBeenCalledWith("e-1");
  });

  it("cannot approve one's own request, and a rejection needs a reason", async () => {
    const reject = vi.fn(async () => ({ data: execution({ status: "rejected" }), status: 200 }));
    const api = fakeApi({ executions: vi.fn(async () => [execution({ actor_id: "me" })]), reject });
    renderPage(<ApprovalsPage />, { api, path: "/approvals", route: "/approvals", roles: ["approver"], sub: "me" });
    const user = userEvent.setup();
    expect(await screen.findByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getAllByText(/by you/).length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "Reject…" }));
    const confirm = screen.getByRole("button", { name: "Reject" });
    expect(confirm).toBeDisabled();
    await user.type(screen.getByLabelText("Reason for rejecting"), "not inspected yet");
    await user.click(confirm);
    expect(reject).toHaveBeenCalledWith("e-1", "not inspected yet");
  });

  it("shows an agent's proposal with its rationale, and the person it acts for cannot approve it", async () => {
    const proposal = execution({
      action: "proposeGrounding",
      actor_type: "agent",
      actor_id: "svc-s2-agent-0001",
      on_behalf_of: "supervisor",
      rationale: "Actuator health fell from 0.95 to 0.68 in 30 minutes.",
      parameters: { droneId: "DRN-0003", reason: "falling" },
    });
    const api = fakeApi({ executions: vi.fn(async () => [proposal]) });
    renderPage(<ApprovalsPage />, { api, path: "/approvals", route: "/approvals", roles: ["approver"], sub: "s-sup", username: "supervisor" });
    expect(await screen.findByText("Actuator health fell from 0.95 to 0.68 in 30 minutes.")).toBeInTheDocument();
    expect(screen.getAllByText("agent").length).toBeGreaterThan(0);
    expect(screen.getByText(/by agent svc-s2-agent on behalf of supervisor/)).toBeInTheDocument();
    const approve = screen.getByRole("button", { name: "Approve" });
    expect(approve).toBeDisabled();
    expect(approve).toHaveAttribute("title", expect.stringMatching(/on your behalf/));
  });

  it("another approver can approve an agent's proposal", async () => {
    const proposal = execution({ actor_type: "agent", actor_id: "svc-s2-agent", on_behalf_of: "supervisor", rationale: "r" });
    const approve = vi.fn(async () => ({ data: execution({ status: "succeeded" }), status: 200 }));
    const api = fakeApi({ executions: vi.fn(async () => [proposal]), approve });
    renderPage(<ApprovalsPage />, { api, path: "/approvals", route: "/approvals", roles: ["approver"], sub: "s-app", username: "approver" });
    await userEvent.setup().click(await screen.findByRole("button", { name: "Approve" }));
    expect(approve).toHaveBeenCalledWith("e-1");
  });

  it("people without the approver role see the queue but no buttons", async () => {
    const api = fakeApi({ executions: vi.fn(async () => [execution()]) });
    renderPage(<ApprovalsPage />, { api, path: "/approvals", route: "/approvals", roles: ["operator"] });
    expect(await screen.findByText(/You need the approver role/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByText("returnDroneToService").length).toBeGreaterThan(0));
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });
});

describe("browsing", () => {
  it("lists object types, then a type's objects", async () => {
    const api = fakeApi({ objectTypes: vi.fn(async () => [DRONE]), objectType: vi.fn(async () => DRONE), objects: vi.fn(async () => [DRN3]) });
    renderPage(<ExplorerPage />, { api, path: "/", route: "/" });
    const card = await screen.findByRole("link", { name: /Commercial drone/ });
    expect(card).toHaveAttribute("href", "/types/CommercialDrone");
    expect(card).toHaveTextContent("1 time series");
    expect(card).toHaveTextContent("from assets.commercial_drone");

    renderPage(<TypePage />, { api, path: "/types/:type", route: "/types/CommercialDrone" });
    const row = await screen.findByRole("link", { name: "Garuda-3" });
    expect(row).toHaveAttribute("href", "/objects/CommercialDrone/DRN-0003");
    expect(screen.getAllByText("READY").length).toBeGreaterThan(0);
  });
});
