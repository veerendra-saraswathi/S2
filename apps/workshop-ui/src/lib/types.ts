// Shapes returned by the ontology engine (services/ontology-engine/src/s2_ontology/api/schemas.py).

export type DataType =
  | "string"
  | "integer"
  | "double"
  | "boolean"
  | "date"
  | "timestamp"
  | "geopoint"
  | "geoshape"
  | "timeseries"
  | "media_ref"
  | string;

export type PropertyType = {
  api_name: string;
  display_name: string;
  description: string | null;
  data_type: DataType;
  is_array: boolean;
  is_primary_key: boolean;
  is_title: boolean;
  is_required: boolean;
  unit: string | null;
  source_column: string | null;
  binding: Record<string, unknown> | null;
  constraints: { enum?: string[]; minimum?: number; maximum?: number; pattern?: string };
  markings: string[];
  status: "draft" | "active" | "deprecated";
};

export type ObjectType = {
  api_name: string;
  display_name: string;
  description: string | null;
  status: "draft" | "active" | "deprecated";
  version: number;
  backing_dataset: string | null;
  markings: string[];
  ui: { icon?: string; color?: string };
  properties: PropertyType[];
};

export type LinkType = {
  api_name: string;
  display_name: string;
  reverse_api_name: string;
  reverse_display_name: string;
  description: string | null;
  source_object_type: string;
  target_object_type: string;
  cardinality: string;
};

export type JsonSchema = {
  type?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  enum?: (string | number)[];
  minLength?: number;
  maxLength?: number;
  minimum?: number;
  maximum?: number;
  description?: string;
  title?: string;
  default?: unknown;
};

export type ActionType = {
  api_name: string;
  display_name: string;
  description: string | null;
  target_object_type: string | null;
  status: string;
  version: number;
  parameters: JsonSchema;
  requires_approval: boolean;
  agent_access: string;
};

export type GeoJsonGeometry = { type: string; coordinates: unknown };

export type OntologyObject = {
  object_type: string;
  primary_key: string;
  properties: Record<string, unknown>;
  version: number;
  created_at: string;
  updated_at: string;
};

export type Writeback = {
  id: string;
  connector: string;
  operation: string;
  status: "pending" | "delivered" | "dead";
  attempts: number;
  delivered_at: string | null;
};

export type ExecutionStatus = "pending_approval" | "rejected" | "succeeded" | "failed";

export type ActionExecution = {
  id: string;
  action: string;
  action_type_version: number;
  status: ExecutionStatus;
  parameters: Record<string, unknown>;
  actor_type: string;
  actor_id: string;
  on_behalf_of: string | null;
  approved_by: string | null;
  policy_decision_id: string | null;
  error: { message?: string } | null;
  created_at: string;
  completed_at: string | null;
  writebacks: Writeback[];
};

export type AuditEntry = {
  seq: number;
  occurred_at: string;
  actor_type: string;
  actor_id: string;
  on_behalf_of: string | null;
  event_type: string;
  resource_type: string | null;
  resource_id: string | null;
  outcome: string;
  policy_decision_id: string | null;
  details: Record<string, unknown>;
  redacted: boolean;
};

export type Timeseries = {
  object_type: string;
  primary_key: string;
  property: string;
  metric: string;
  unit: string | null;
  points: { time: string; value: number }[];
};

export type Me = {
  subject: string;
  tenant_id: string;
  roles: string[];
  actor_type: string;
  username: string | null;
};

export type TriageRule = {
  api_name: string;
  display_name: string;
  description: string | null;
  object_type: string;
  severity: "low" | "medium" | "high" | "critical";
  conditions: { property: string; op: string; value: unknown; max_age_s?: number }[];
  actions: string[];
  version: number;
};

export type Evidence = { property: string; op: string; expected: unknown; actual: unknown; observed_at: string | null };

export type TriageQueue = {
  rule: TriageRule;
  count: number;
  warning: string | null;
  items: { object: OntologyObject; evidence: Evidence[] }[] | null;
};
