import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { vi } from "vitest";

import { Api } from "../lib/api";
import { AuthContext, type Auth } from "../lib/auth";
import type { ActionType, LinkType, ObjectType, OntologyObject } from "../lib/types";

export const DRONE: ObjectType = {
  api_name: "CommercialDrone",
  display_name: "Commercial drone",
  description: "An unmanned aerial vehicle.",
  status: "active",
  version: 2,
  backing_dataset: "assets.commercial_drone",
  markings: [],
  ui: { color: "#534AB7" },
  properties: [
    prop("droneId", "Drone ID", { is_primary_key: true, is_required: true, source_column: "drone_id" }),
    prop("callSign", "Call sign", { is_title: true, is_required: true }),
    prop("status", "Status", { constraints: { enum: ["READY", "GROUNDED"] } }),
    prop("firmwareVersion", "Firmware version", { source_column: "firmware_version" }),
    prop("position", "Last known position", { data_type: "geopoint" }),
    prop("actuatorHealth", "Actuator health", { data_type: "timeseries", markings: ["internal"] }),
  ],
};

export const DRN3: OntologyObject = {
  object_type: "CommercialDrone",
  primary_key: "DRN-0003",
  properties: {
    droneId: "DRN-0003",
    callSign: "Garuda-3",
    status: "READY",
    firmwareVersion: "4.3.0",
    position: { type: "Point", coordinates: [78.3483, 17.4239] },
  },
  version: 6,
  created_at: "2026-11-14T21:29:45Z",
  updated_at: new Date().toISOString(),
};

export const GROUND: ActionType = {
  api_name: "groundDrone",
  display_name: "Ground drone",
  description: "Take a drone out of service.",
  target_object_type: "CommercialDrone",
  status: "active",
  version: 1,
  requires_approval: false,
  agent_access: "none",
  parameters: {
    type: "object",
    required: ["droneId", "reason"],
    properties: { droneId: { type: "string" }, reason: { type: "string", minLength: 3 } },
  },
};

export const RETURN: ActionType = {
  ...GROUND,
  api_name: "returnDroneToService",
  display_name: "Return drone to service",
  requires_approval: true,
  parameters: { type: "object", required: ["droneId"], properties: { droneId: { type: "string" }, note: { type: "string" } } },
};

export const INSPECTS: LinkType = {
  api_name: "inspects",
  display_name: "inspects",
  reverse_api_name: "inspectedBy",
  reverse_display_name: "inspected by",
  description: null,
  source_object_type: "CommercialDrone",
  target_object_type: "Substation",
  cardinality: "many_to_many",
};

function prop(api_name: string, display_name: string, extra: Partial<ObjectType["properties"][number]> = {}) {
  return {
    api_name,
    display_name,
    description: null,
    data_type: "string",
    is_array: false,
    is_primary_key: false,
    is_title: false,
    is_required: false,
    unit: null,
    source_column: null,
    binding: null,
    constraints: {},
    markings: [],
    status: "active" as const,
    ...extra,
  };
}

/** An Api whose methods are mocks; pass the ones a test cares about. */
export function fakeApi(overrides: Partial<Record<keyof Api, unknown>> = {}): Api {
  const api = new Api(() => "token", "/api", vi.fn());
  const never = () => new Promise(() => {});
  for (const key of Object.keys(api) as (keyof Api)[]) {
    if (typeof api[key] === "function") (api as unknown as Record<string, unknown>)[key] = vi.fn(never);
  }
  Object.assign(api, overrides);
  return api;
}

export function renderPage(
  element: ReactNode,
  { api, path, route, roles = ["viewer"], sub = "me" }: { api: Api; path: string; route: string; roles?: string[]; sub?: string },
) {
  const auth: Auth = {
    user: { access_token: "token", profile: { sub, preferred_username: "tester" } } as unknown as Auth["user"],
    ready: true,
    roles,
    username: "tester",
    api,
    login: vi.fn(),
    logout: vi.fn(),
    hasRole: (...wanted: string[]) => wanted.some((r) => roles.includes(r)),
  };
  const router = createMemoryRouter([{ path, element }, { path: "*", element: <p>elsewhere</p> }], { initialEntries: [route] });
  return render(
    <AuthContext.Provider value={auth}>
      <RouterProvider router={router} />
    </AuthContext.Provider>,
  );
}
