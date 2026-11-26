// The ontology engine's API, through the dev server's /api proxy (vite.config.ts).

import type {
  ActionExecution,
  ActionType,
  AuditEntry,
  LinkType,
  Me,
  ObjectType,
  OntologyObject,
  Timeseries,
  TriageQueue,
} from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: Record<string, unknown>;

  constructor(status: number, message: string, detail: Record<string, unknown> = {}) {
    super(message);
    this.status = status;
    this.detail = detail;
  }

  /** Reasons a policy gave for refusing, when it did. */
  get reasons(): string[] {
    const reasons = this.detail.reasons;
    return Array.isArray(reasons) ? reasons.map(String) : [];
  }
}

/** The engine answers {"detail": {"message": ...}}, or FastAPI's own {"detail": [...]} for 422s. */
export async function toApiError(response: Response): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    // not JSON
  }
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const d = detail as Record<string, unknown>;
    return new ApiError(response.status, String(d.message ?? response.statusText), d);
  }
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { msg?: string; loc?: unknown[] };
    const where = Array.isArray(first.loc) ? first.loc.slice(1).join(".") : "";
    return new ApiError(response.status, `${where ? `${where}: ` : ""}${first.msg ?? "invalid request"}`);
  }
  return new ApiError(response.status, typeof detail === "string" ? detail : response.statusText || `HTTP ${response.status}`);
}

export type TokenSource = () => string | null;

export class Api {
  private readonly token: TokenSource;
  private readonly base: string;
  private readonly fetcher: typeof fetch;

  constructor(token: TokenSource, base = "/api", fetcher: typeof fetch = (...args) => fetch(...args)) {
    this.token = token;
    this.base = base;
    this.fetcher = fetcher;
  }

  async request<T>(method: string, path: string, body?: unknown, headers: Record<string, string> = {}): Promise<{ data: T; status: number }> {
    const token = this.token();
    const response = await this.fetcher(`${this.base}${path}`, {
      method,
      headers: {
        Accept: "application/json",
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...headers,
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (!response.ok) throw await toApiError(response);
    const data = (response.status === 204 ? null : await response.json()) as T;
    return { data, status: response.status };
  }

  async get<T>(path: string): Promise<T> {
    return (await this.request<T>("GET", path)).data;
  }

  me = () => this.get<Me>("/v1/me");
  objectTypes = () => this.get<ObjectType[]>("/v1/ontology/object-types");
  objectType = (name: string) => this.get<ObjectType>(`/v1/ontology/object-types/${enc(name)}`);
  linkTypes = () => this.get<LinkType[]>("/v1/ontology/link-types");
  actionTypes = () => this.get<ActionType[]>("/v1/ontology/action-types");

  objects = (type: string, opts: { limit?: number; after?: string } = {}) =>
    this.get<OntologyObject[]>(`/v1/objects/${enc(type)}${query({ limit: opts.limit ?? 100, after: opts.after })}`);
  object = (type: string, key: string) => this.get<OntologyObject>(`/v1/objects/${enc(type)}/${enc(key)}`);
  linked = (type: string, key: string, link: string) =>
    this.get<OntologyObject[]>(`/v1/objects/${enc(type)}/${enc(key)}/links/${enc(link)}`);
  timeseries = (type: string, key: string, property: string, opts: { start?: string; limit?: number } = {}) =>
    this.get<Timeseries>(
      `/v1/objects/${enc(type)}/${enc(key)}/timeseries/${enc(property)}${query({ start: opts.start, limit: opts.limit })}`,
    );

  /** 200: ran. 202: awaits approval. Errors (403 policy, 409 failed, 422 invalid) throw ApiError. */
  applyAction = (name: string, parameters: Record<string, unknown>, idempotencyKey: string) =>
    this.request<ActionExecution>("POST", `/v1/actions/${enc(name)}/apply`, { parameters }, { "Idempotency-Key": idempotencyKey });
  /** `param` keeps executions whose parameters include each name with that value. */
  executions = (opts: { status?: string; action?: string; limit?: number; param?: Record<string, string> } = {}) => {
    const { param = {}, ...rest } = opts;
    const named = Object.entries(param).map(([name, value]) => `param=${enc(`${name}=${value}`)}`);
    const base = query(rest);
    const extra = named.join("&");
    return this.get<ActionExecution[]>(`/v1/action-executions${base}${extra ? `${base ? "&" : "?"}${extra}` : ""}`);
  };
  approve = (id: string) => this.request<ActionExecution>("POST", `/v1/action-executions/${enc(id)}/approve`);
  reject = (id: string, reason: string) =>
    this.request<ActionExecution>("POST", `/v1/action-executions/${enc(id)}/reject`, { reason });

  triageQueues = () => this.get<TriageQueue[]>("/v1/triage/queues");
  triageQueue = (name: string) => this.get<TriageQueue>(`/v1/triage/queues/${enc(name)}`);

  audit = (opts: { event_type?: string; resource_type?: string; resource_id?: string; before_seq?: number; limit?: number } = {}) =>
    this.get<AuditEntry[]>(`/v1/audit${query(opts)}`);
  verifyAudit = () => this.get<{ entries: number; verified: boolean; first_bad_seq: number | null }>("/v1/audit/verify");
}

function enc(part: string): string {
  return encodeURIComponent(part);
}

export function query(params: Record<string, string | number | undefined | null>): string {
  const pairs = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "");
  if (pairs.length === 0) return "";
  return `?${new URLSearchParams(pairs.map(([k, v]) => [k, String(v)])).toString()}`;
}
