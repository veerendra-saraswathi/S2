"""Pydantic contracts: what the API accepts and returns, and what an ontology pack
(ontologies/<pack>/pack.yaml) deserialises into.

Checks that need only the pack itself live here. Checks that need the registry (does
that object type exist, is that property writable) live in domain/registry.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_serializer, model_validator

from s2_common.opa import POLICY_PATH_PATTERN
from s2_ontology.db.models import (
    CAMEL,
    GEO_TYPES,
    PASCAL,
    ActorType,
    AgentAccess,
    AuditOutcome,
    Cardinality,
    ExecutionStatus,
    LifecycleStatus,
    OutboxStatus,
    PropertyDataType,
)

PascalName = Annotated[str, StringConstraints(pattern=PASCAL)]
CamelName = Annotated[str, StringConstraints(pattern=CAMEL)]
PolicyPath = Annotated[str, StringConstraints(pattern=POLICY_PATH_PATTERN)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- #
# Definitions (ontology packs)
# --------------------------------------------------------------------------- #


class PropertyTypeIn(_In):
    api_name: CamelName
    display_name: str
    description: str | None = None
    data_type: PropertyDataType
    is_array: bool = False
    is_primary_key: bool = False
    is_title: bool = False
    is_required: bool = False
    unit: str | None = None
    source_column: str | None = None
    binding: dict[str, Any] | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    markings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> PropertyTypeIn:
        kind = self.data_type
        if kind == PropertyDataType.TIMESERIES:
            if not (self.binding and isinstance(self.binding.get("metric"), str)):
                raise ValueError(f"{self.api_name}: a timeseries property needs binding.metric")
            if self.is_required:
                raise ValueError(f"{self.api_name}: a timeseries property cannot be required")
        if self.is_array and (kind == PropertyDataType.TIMESERIES or kind in GEO_TYPES):
            raise ValueError(f"{self.api_name}: a {kind.value} property cannot be an array")
        if self.is_primary_key:
            if kind not in (PropertyDataType.STRING, PropertyDataType.INTEGER) or self.is_array:
                raise ValueError(f"{self.api_name}: the primary key must be a scalar string or integer")
            self.is_required = True
        return self


class ObjectTypeIn(_In):
    api_name: PascalName
    display_name: str
    description: str | None = None
    backing_dataset: str | None = None
    markings: list[str] = Field(default_factory=list)
    ui: dict[str, Any] = Field(default_factory=dict)
    properties: list[PropertyTypeIn] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> ObjectTypeIn:
        names = [p.api_name for p in self.properties]
        if len(names) != len(set(names)):
            raise ValueError(f"{self.api_name}: duplicate property api_name")
        if sum(p.is_primary_key for p in self.properties) != 1:
            raise ValueError(f"{self.api_name}: exactly one property must be the primary key")
        if any(p.is_primary_key and p.markings for p in self.properties):
            # It would hide an object's identity from people who can see the object.
            raise ValueError(f"{self.api_name}: the primary key cannot carry markings; mark the type instead")
        if sum(p.is_title for p in self.properties) > 1:
            raise ValueError(f"{self.api_name}: at most one property may be the title")
        if sum(p.data_type in GEO_TYPES for p in self.properties) > 1:
            # An object has one geometry column.
            raise ValueError(f"{self.api_name}: at most one geopoint/geoshape property is supported")
        return self


class LinkTypeIn(_In):
    api_name: CamelName
    display_name: str
    reverse_api_name: CamelName
    reverse_display_name: str
    description: str | None = None
    source_object_type: PascalName
    target_object_type: PascalName
    cardinality: Cardinality


class OntologyEdit(_In):
    """One step of an action. String values may reference inputs as `{{ params.<name> }}`."""

    op: Literal["create_object", "modify_object", "delete_object", "create_link", "delete_link"]
    object_type: PascalName  # the type being edited; for link edits, the link's source type
    target: str  # primary key of the object (for link edits: the source object)
    assign: dict[str, Any] = Field(default_factory=dict)  # create_object / modify_object
    link_type: CamelName | None = None  # link edits: the link's forward name
    link_target: str | None = None  # link edits: primary key of the object at the far end

    @model_validator(mode="after")
    def _check(self) -> OntologyEdit:
        is_link = self.op in ("create_link", "delete_link")
        if is_link and not (self.link_type and self.link_target):
            raise ValueError(f"{self.op}: requires link_type and link_target")
        if not is_link and (self.link_type or self.link_target):
            raise ValueError(f"{self.op}: link_type and link_target only apply to link edits")
        if self.op == "modify_object" and not self.assign:
            raise ValueError("modify_object: assign must set at least one property")
        if self.op not in ("create_object", "modify_object") and self.assign:
            raise ValueError(f"{self.op}: does not take assign")
        return self


class WritebackSpec(_In):
    connector: str = Field(min_length=1)  # cmms, sap-pm, samanvaya-fleet
    operation: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class ActionTypeIn(_In):
    api_name: CamelName
    display_name: str
    description: str | None = None
    target_object_type: PascalName | None = None
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    edits: list[OntologyEdit] = Field(min_length=1)
    writeback: list[WritebackSpec] = Field(default_factory=list)
    policy_path: PolicyPath = "s2/actions/standard"
    requires_approval: bool = False
    agent_access: AgentAccess = AgentAccess.NONE

    @model_validator(mode="after")
    def _check(self) -> ActionTypeIn:
        if self.agent_access == AgentAccess.PROPOSE and not self.requires_approval:
            raise ValueError(f"{self.api_name}: an agent-proposable action must require human approval")
        return self


class OntologyPack(_In):
    """A reviewable, versioned bundle of definitions."""

    name: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    version: str
    description: str | None = None
    requires: list[str] = Field(default_factory=list)  # packs that must be applied first
    object_types: list[ObjectTypeIn] = Field(default_factory=list)
    link_types: list[LinkTypeIn] = Field(default_factory=list)
    action_types: list[ActionTypeIn] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #


class Provenance(_In):
    """Where a write came from, when a pipeline made it. Recorded in the audit entry."""

    dataset: str = Field(pattern=r"^[a-z0-9_]+(\.[a-z0-9_]+)+$", examples=["assets.commercial_drone"])
    # A string: Iceberg snapshot ids are 64-bit and do not survive JSON numbers in a browser.
    snapshot_id: str = Field(pattern=r"^-?[0-9]{1,20}$")
    branch: str = Field(default="main", min_length=1, max_length=200)
    run_id: str | None = Field(default=None, max_length=200)


class ObjectWrite(_In):
    properties: dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance | None = None


class LinkWrite(_In):
    target_primary_key: str = Field(min_length=1)


class ActionRequest(_In):
    parameters: dict[str, Any] = Field(default_factory=dict)


class RejectRequest(_In):
    reason: str = Field(min_length=1, max_length=2000)


# --------------------------------------------------------------------------- #
# Responses
# --------------------------------------------------------------------------- #


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Me(BaseModel):
    subject: str
    username: str | None
    tenant_id: uuid.UUID
    roles: list[str]
    actor_type: str
    on_behalf_of: str | None


class PropertyTypeOut(_Out):
    api_name: str
    display_name: str
    description: str | None
    data_type: PropertyDataType
    is_array: bool
    is_primary_key: bool
    is_title: bool
    is_required: bool
    unit: str | None
    source_column: str | None
    binding: dict[str, Any] | None
    constraints: dict[str, Any]
    markings: list[str]
    status: LifecycleStatus


class ObjectTypeOut(_Out):
    api_name: str
    display_name: str
    description: str | None
    status: LifecycleStatus
    version: int
    backing_dataset: str | None
    markings: list[str]
    ui: dict[str, Any]
    properties: list[PropertyTypeOut]


class LinkTypeOut(BaseModel):
    api_name: str
    display_name: str
    reverse_api_name: str
    reverse_display_name: str
    description: str | None
    source_object_type: str
    target_object_type: str
    cardinality: Cardinality
    status: LifecycleStatus


class ActionTypeOut(BaseModel):
    api_name: str
    display_name: str
    description: str | None
    target_object_type: str | None
    status: LifecycleStatus
    version: int
    parameters: dict[str, Any]
    edits: list[dict[str, Any]]
    writeback: list[dict[str, Any]]
    policy_path: str
    requires_approval: bool
    agent_access: AgentAccess


class PackApplied(BaseModel):
    pack: str
    version: str
    created: list[str] = Field(default_factory=list)
    updated: list[str] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)


class ObjectOut(BaseModel):
    object_type: str
    primary_key: str
    properties: dict[str, Any]
    version: int
    created_at: datetime
    updated_at: datetime


class TimeseriesPoint(BaseModel):
    time: datetime
    value: float


class TimeseriesOut(BaseModel):
    object_type: str
    primary_key: str
    property: str
    metric: str
    unit: str | None
    points: list[TimeseriesPoint]


class WritebackOut(_Out):
    id: uuid.UUID
    connector: str
    operation: str
    payload: dict[str, Any]
    status: OutboxStatus
    attempts: int
    delivered_at: datetime | None


class ActionExecutionOut(BaseModel):
    id: uuid.UUID
    action: str
    action_type_version: int
    status: ExecutionStatus
    parameters: dict[str, Any]
    actor_type: ActorType
    actor_id: str
    on_behalf_of: str | None = None
    approved_by: str | None
    policy_decision_id: str | None
    error: dict[str, Any] | None
    created_at: datetime
    completed_at: datetime | None
    writebacks: list[WritebackOut]


class AuditEntryOut(_Out):
    seq: int
    occurred_at: datetime
    actor_type: ActorType
    actor_id: str
    on_behalf_of: str | None
    event_type: str
    resource_type: str | None
    resource_id: str | None
    outcome: AuditOutcome
    policy_decision_id: str | None
    trace_id: str | None
    details: dict[str, Any]
    entry_hash: bytes
    # True when markings took something out of `details` (or out of resource_id) for this
    # caller. `entry_hash` is over the stored entry, so a redacted entry cannot be
    # re-hashed by the client; GET /v1/audit/verify checks the stored chain.
    redacted: bool = False

    @field_serializer("entry_hash")
    def _hex(self, value: bytes) -> str:
        return value.hex()


class AuditVerification(BaseModel):
    entries: int
    verified: bool
    first_bad_seq: int | None
