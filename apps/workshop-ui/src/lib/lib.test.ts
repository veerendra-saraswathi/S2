import { describe, expect, it, vi } from "vitest";

import { chartGeometry } from "../components/LineChart";
import { coerce } from "../components/ParamForm";
import { DRN3, DRONE } from "../test/fixtures";
import { Api, ApiError, query, toApiError } from "./api";
import { rolesOf } from "./auth";
import { bounds, toFeatures } from "./geo";
import { columnProperties, formatValue, keyParameter, titleOf } from "./ontology";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("errors", () => {
  it("reads the engine's message and policy reasons", async () => {
    const error = await toApiError(json(403, { detail: { message: "denied by policy", reasons: ["the requester cannot approve their own request"] } }));
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(403);
    expect(error.message).toBe("denied by policy");
    expect(error.reasons).toEqual(["the requester cannot approve their own request"]);
  });

  it("reads FastAPI's validation errors", async () => {
    const error = await toApiError(json(422, { detail: [{ loc: ["body", "parameters", "reason"], msg: "Field required" }] }));
    expect(error.message).toBe("parameters.reason: Field required");
  });

  it("copes with a body that is not JSON", async () => {
    const error = await toApiError(new Response("upstream down", { status: 502, statusText: "Bad Gateway" }));
    expect(error.message).toBe("Bad Gateway");
  });
});

describe("the API client", () => {
  it("sends the token, the idempotency key and the parameters", async () => {
    const fetcher = vi.fn(async () => json(202, { id: "e1", status: "pending_approval" }));
    const api = new Api(() => "tok", "/api", fetcher as unknown as typeof fetch);
    const { status } = await api.applyAction("returnDroneToService", { droneId: "DRN-0003" }, "key-1");
    expect(status).toBe(202);
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/actions/returnDroneToService/apply");
    expect(init.headers).toMatchObject({ Authorization: "Bearer tok", "Idempotency-Key": "key-1" });
    expect(JSON.parse(String(init.body))).toEqual({ parameters: { droneId: "DRN-0003" } });
  });

  it("encodes path parts and drops empty query values", async () => {
    const fetcher = vi.fn(async () => json(200, []));
    const api = new Api(() => null, "/api", fetcher as unknown as typeof fetch);
    await api.object("CommercialDrone", "A/B 1");
    await api.audit({ resource_id: "DRN-0003", event_type: undefined });
    expect(fetcher.mock.calls.map((c) => (c as unknown as [string])[0])).toEqual([
      "/api/v1/objects/CommercialDrone/A%2FB%201",
      "/api/v1/audit?resource_id=DRN-0003",
    ]);
    expect(query({})).toBe("");
  });

  it("throws ApiError on failure", async () => {
    const api = new Api(() => null, "/api", (async () => json(404, { detail: { message: "no such thing" } })) as unknown as typeof fetch);
    await expect(api.object("X", "1")).rejects.toMatchObject({ status: 404, message: "no such thing" });
  });
});

describe("tokens", () => {
  it("reads realm roles, and nothing from a bad token", () => {
    const payload = btoa(JSON.stringify({ realm_access: { roles: ["operator", "marking:internal"] } }))
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
    expect(rolesOf(`h.${payload}.s`)).toEqual(["operator", "marking:internal"]);
    expect(rolesOf("not-a-jwt")).toEqual([]);
    expect(rolesOf(undefined)).toEqual([]);
  });
});

describe("ontology helpers", () => {
  it("titles, columns, values, the key parameter", () => {
    expect(titleOf(DRONE, DRN3)).toBe("Garuda-3");
    expect(titleOf(DRONE, { ...DRN3, properties: {} })).toBe("DRN-0003");
    expect(columnProperties(DRONE).map((p) => p.api_name)).toEqual(["status", "firmwareVersion"]);
    expect(formatValue(DRN3.properties.position, DRONE.properties[4])).toBe("17.4239, 78.3483");
    expect(formatValue(undefined)).toBe("—");
    expect(formatValue(3.5, { ...DRONE.properties[3], unit: "V" })).toBe("3.5 V");
    expect(keyParameter(DRONE, ["droneId", "reason"])).toBe("droneId");
    expect(keyParameter(DRONE, ["workOrderId"])).toBeUndefined();
  });
});

describe("the map's data", () => {
  it("makes a feature per placed object, with its type's colour, and a bounding box", () => {
    const unplaced = { ...DRN3, primary_key: "DRN-0009", properties: { callSign: "x" } };
    const features = toFeatures(DRONE, [DRN3, unplaced]);
    expect(features).toHaveLength(1);
    expect(features[0].properties).toEqual({ objectType: "CommercialDrone", key: "DRN-0003", title: "Garuda-3", color: "#534AB7", status: "READY" });
    expect(bounds(features)).toEqual([
      [78.3483, 17.4239],
      [78.3483, 17.4239],
    ]);
    expect(bounds([])).toBeNull();
  });
});

describe("the chart", () => {
  it("scales points into the box with the y axis up", () => {
    const g = chartGeometry(
      [
        { time: "2026-11-17T10:00:00Z", value: 1 },
        { time: "2026-11-17T10:00:10Z", value: 3 },
      ],
      100,
      50,
      0,
    )!;
    expect(g.path).toBe("M0.0,50.0 L100.0,0.0");
    expect([g.min, g.max]).toEqual([1, 3]);
    expect(chartGeometry([], 10, 10)).toBeNull();
    expect(chartGeometry([{ time: "2026-11-17T10:00:00Z", value: 5 }], 100, 50, 0)!.path).toBe("M50.0,25.0");
  });
});

describe("parameter forms", () => {
  it("coerces strings to the schema and reports what is wrong", () => {
    const schema = {
      type: "object",
      required: ["reason", "count"],
      properties: { reason: { type: "string", minLength: 3 }, count: { type: "integer" }, note: { type: "string" }, urgent: { type: "boolean" } },
    };
    expect(coerce(schema, { reason: " ok ", count: "2.5", note: "", urgent: true })).toEqual({
      values: { urgent: true },
      errors: { reason: "at least 3 characters", count: "a whole number" },
    });
    expect(coerce(schema, { reason: "sensor drift", count: "2", note: "", urgent: false })).toEqual({
      values: { reason: "sensor drift", count: 2, urgent: false },
      errors: {},
    });
  });
});
