"""The metamodel registry: applying ontology packs and reading definitions.

Applying a pack is an idempotent upsert, all or nothing. Additive and cosmetic changes
go through. Changes that would reinterpret stored data or break an API identifier are
refused. A property that disappears from a pack is deprecated, never dropped, so stored
values stay readable.
"""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, selectinload

from s2_common.auth import Principal
from s2_ontology.api.schemas import (
    ActionTypeIn,
    ActionTypeOut,
    LinkTypeIn,
    LinkTypeOut,
    ObjectTypeIn,
    OntologyPack,
    PackApplied,
    PropertyTypeIn,
)
from s2_ontology.db.models import (
    ActionType,
    LifecycleStatus,
    LinkType,
    ObjectInstance,
    ObjectType,
    PropertyDataType,
    PropertyType,
)
from s2_ontology.domain import audit
from s2_ontology.domain.errors import Invalid, NotFound
from s2_ontology.domain.templates import referenced_params

_IMMUTABLE_PROPERTY_FIELDS = ("data_type", "is_array", "is_primary_key")
_MUTABLE_PROPERTY_FIELDS = (
    "display_name",
    "description",
    "is_title",
    "is_required",
    "unit",
    "source_column",
    "binding",
    "constraints",
    "markings",
)
_OBJECT_TYPE_FIELDS = ("display_name", "description", "backing_dataset", "markings", "ui")
_LINK_TYPE_FIELDS = ("display_name", "reverse_display_name", "description")


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


def find_object_type(session: Session, api_name: str) -> ObjectType | None:
    query = (
        select(ObjectType).where(ObjectType.api_name == api_name).options(selectinload(ObjectType.properties))
    )
    return session.scalar(query)


def get_object_type(session: Session, api_name: str) -> ObjectType:
    object_type = find_object_type(session, api_name)
    if object_type is None:
        raise NotFound(f"object type '{api_name}' does not exist")
    return object_type


def list_object_types(session: Session) -> list[ObjectType]:
    query = select(ObjectType).options(selectinload(ObjectType.properties)).order_by(ObjectType.api_name)
    return list(session.scalars(query))


def find_link_type(session: Session, source: ObjectType, api_name: str) -> LinkType | None:
    query = select(LinkType).where(LinkType.source_object_type_id == source.id, LinkType.api_name == api_name)
    return session.scalar(query)


def list_link_types(session: Session) -> list[LinkType]:
    return list(session.scalars(select(LinkType).order_by(LinkType.api_name)))


def link_type_out(link: LinkType) -> LinkTypeOut:
    return LinkTypeOut(
        api_name=link.api_name,
        display_name=link.display_name,
        reverse_api_name=link.reverse_api_name,
        reverse_display_name=link.reverse_display_name,
        description=link.description,
        source_object_type=link.source_object_type.api_name,
        target_object_type=link.target_object_type.api_name,
        cardinality=link.cardinality,
        status=link.status,
    )


def find_action_type(session: Session, api_name: str) -> ActionType | None:
    return session.scalar(select(ActionType).where(ActionType.api_name == api_name))


def get_action_type(session: Session, api_name: str) -> ActionType:
    action = find_action_type(session, api_name)
    if action is None:
        raise NotFound(f"action '{api_name}' does not exist")
    return action


def list_action_types(session: Session) -> list[ActionType]:
    return list(session.scalars(select(ActionType).order_by(ActionType.api_name)))


def action_type_out(action: ActionType) -> ActionTypeOut:
    target = action.target_object_type
    return ActionTypeOut(
        api_name=action.api_name,
        display_name=action.display_name,
        description=action.description,
        target_object_type=target.api_name if target else None,
        status=action.status,
        version=action.version,
        parameters=action.parameters,
        edits=action.edits,
        writeback=action.writeback,
        policy_path=action.policy_path,
        requires_approval=action.requires_approval,
        agent_access=action.agent_access,
    )


# --------------------------------------------------------------------------- #
# Applying a pack
# --------------------------------------------------------------------------- #


def apply_pack(session: Session, principal: Principal, pack: OntologyPack) -> PackApplied:
    """Upsert everything the pack defines. The caller commits; any error leaves nothing behind."""
    result = PackApplied(pack=pack.name, version=pack.version)
    hint = f" (pack '{pack.name}' requires: {', '.join(pack.requires)})" if pack.requires else ""

    for object_spec in pack.object_types:
        _upsert_object_type(session, principal, object_spec, result)
    session.flush()
    for link_spec in pack.link_types:
        _upsert_link_type(session, principal, link_spec, result, hint)
    session.flush()
    for action_spec in pack.action_types:
        _upsert_action_type(session, principal, action_spec, result, hint)
    session.flush()

    audit.record(
        session,
        principal,
        "ontology.pack.applied",
        resource_type="ontology_pack",
        resource_id=pack.name,
        details=result.model_dump(),
    )
    return result


def _new_property(principal: Principal, spec: PropertyTypeIn) -> PropertyType:
    return PropertyType(tenant_id=principal.tenant_id, created_by=principal.subject, **spec.model_dump())


def _upsert_object_type(
    session: Session, principal: Principal, spec: ObjectTypeIn, result: PackApplied
) -> None:
    label = f"ObjectType:{spec.api_name}"
    existing = find_object_type(session, spec.api_name)
    if existing is None:
        row = ObjectType(
            tenant_id=principal.tenant_id,
            created_by=principal.subject,
            status=LifecycleStatus.ACTIVE,
            **spec.model_dump(exclude={"properties"}),
        )
        row.properties = [_new_property(principal, p) for p in spec.properties]
        session.add(row)
        result.created.append(label)
        return

    current = {p.api_name: p for p in existing.properties}
    wanted = {p.api_name: p for p in spec.properties}
    has_objects = bool(session.scalar(select(exists().where(ObjectInstance.object_type_id == existing.id))))
    changed = False

    # Release a title that is moving first: only one property may hold it at a time.
    for name, prop in current.items():
        if prop.is_title and not (name in wanted and wanted[name].is_title):
            prop.is_title = False
            changed = True
    session.flush()

    for name, prop_spec in wanted.items():
        where = f"{spec.api_name}.{name}"
        prop = current.get(name)
        if prop is None:
            if prop_spec.is_required and has_objects:
                raise Invalid(
                    f"{where}: a required property cannot be added while objects of this type exist"
                )
            existing.properties.append(_new_property(principal, prop_spec))
            changed = True
            continue
        for field in _IMMUTABLE_PROPERTY_FIELDS:
            old, new = getattr(prop, field), getattr(prop_spec, field)
            if old != new:
                raise Invalid(
                    f"{where}: changing {field} would reinterpret existing data "
                    f"({_show(old)} -> {_show(new)}); add a new property instead"
                )
        if prop_spec.is_required and not prop.is_required and has_objects:
            raise Invalid(f"{where}: cannot become required while objects of this type exist")
        for field in _MUTABLE_PROPERTY_FIELDS:
            new = getattr(prop_spec, field)
            if getattr(prop, field) != new:
                setattr(prop, field, new)
                changed = True
        if prop.status != LifecycleStatus.ACTIVE:
            prop.status = LifecycleStatus.ACTIVE
            changed = True

    for name, prop in current.items():
        if name in wanted or prop.status == LifecycleStatus.DEPRECATED:
            continue
        if prop.is_primary_key:
            raise Invalid(f"{spec.api_name}.{name}: the primary key cannot be removed")
        prop.status = LifecycleStatus.DEPRECATED  # kept readable, no longer writable
        changed = True

    for field in _OBJECT_TYPE_FIELDS:
        new = getattr(spec, field)
        if getattr(existing, field) != new:
            setattr(existing, field, new)
            changed = True
    if existing.status != LifecycleStatus.ACTIVE:
        existing.status = LifecycleStatus.ACTIVE
        changed = True

    if changed:
        existing.updated_at = func.now()  # touches the row, so `version` advances once per change
        result.updated.append(label)
    else:
        result.unchanged.append(label)


def _upsert_link_type(
    session: Session, principal: Principal, spec: LinkTypeIn, result: PackApplied, hint: str
) -> None:
    label = f"LinkType:{spec.source_object_type}.{spec.api_name}"
    where = f"link type '{spec.api_name}'"
    source = _known_type(session, spec.source_object_type, where, hint)
    target = _known_type(session, spec.target_object_type, where, hint)
    existing = find_link_type(session, source, spec.api_name)
    if existing is None:
        session.add(
            LinkType(
                tenant_id=principal.tenant_id,
                created_by=principal.subject,
                status=LifecycleStatus.ACTIVE,
                source_object_type_id=source.id,
                target_object_type_id=target.id,
                **spec.model_dump(exclude={"source_object_type", "target_object_type"}),
            )
        )
        result.created.append(label)
        return

    if existing.target_object_type_id != target.id:
        raise Invalid(f"{where}: changing the target object type would orphan existing links")
    if existing.cardinality != spec.cardinality:
        raise Invalid(f"{where}: changing cardinality could invalidate existing links")
    if existing.reverse_api_name != spec.reverse_api_name:
        raise Invalid(f"{where}: reverse_api_name is an API identifier and cannot change")

    changed = False
    for field in _LINK_TYPE_FIELDS:
        new = getattr(spec, field)
        if getattr(existing, field) != new:
            setattr(existing, field, new)
            changed = True
    if existing.status != LifecycleStatus.ACTIVE:
        existing.status = LifecycleStatus.ACTIVE
        changed = True
    (result.updated if changed else result.unchanged).append(label)


def _upsert_action_type(
    session: Session, principal: Principal, spec: ActionTypeIn, result: PackApplied, hint: str
) -> None:
    label = f"ActionType:{spec.api_name}"
    where = f"action '{spec.api_name}'"
    _check_action(session, spec, where, hint)
    target = _known_type(session, spec.target_object_type, where, hint) if spec.target_object_type else None

    fields: dict[str, Any] = {
        "display_name": spec.display_name,
        "description": spec.description,
        "target_object_type_id": target.id if target else None,
        "parameters": spec.parameters,
        "edits": [edit.model_dump(mode="json") for edit in spec.edits],
        "writeback": [w.model_dump(mode="json") for w in spec.writeback],
        "policy_path": spec.policy_path,
        "requires_approval": spec.requires_approval,
        "agent_access": spec.agent_access,
        "status": LifecycleStatus.ACTIVE,
    }
    existing = find_action_type(session, spec.api_name)
    if existing is None:
        session.add(
            ActionType(
                tenant_id=principal.tenant_id, created_by=principal.subject, api_name=spec.api_name, **fields
            )
        )
        result.created.append(label)
        return

    changed = False
    for field, new in fields.items():
        if getattr(existing, field) != new:
            setattr(
                existing, field, new
            )  # any change advances `version`; pending requests then need resubmitting
            changed = True
    (result.updated if changed else result.unchanged).append(label)


def _check_action(session: Session, spec: ActionTypeIn, where: str, hint: str) -> None:
    """Refuse an action that could never run, when it is registered rather than when it is used."""
    if spec.parameters.get("type") != "object":
        raise Invalid(f"{where}: parameters is not a valid JSON Schema for an object")
    try:
        Draft202012Validator.check_schema(spec.parameters)
    except SchemaError as exc:
        raise Invalid(f"{where}: parameters is not a valid JSON Schema: {exc.message}") from exc

    templated = [edit.model_dump() for edit in spec.edits] + [w.model_dump() for w in spec.writeback]
    undeclared = sorted(referenced_params(templated) - set(spec.parameters.get("properties") or {}))
    if undeclared:
        raise Invalid(f"{where}: edits or write-backs reference undeclared parameters {undeclared}")

    for index, edit in enumerate(spec.edits):
        step = f"{where} edit {index + 1} ({edit.op})"
        object_type = _known_type(session, edit.object_type, step, hint)
        writable = {
            p.api_name
            for p in object_type.properties
            if p.status == LifecycleStatus.ACTIVE
            and not p.is_primary_key
            and p.data_type != PropertyDataType.TIMESERIES
        }
        unknown = sorted(set(edit.assign) - writable)
        if unknown:
            raise Invalid(f"{step}: {edit.object_type} has no writable property {unknown}")
        if edit.link_type and find_link_type(session, object_type, edit.link_type) is None:
            raise Invalid(f"{step}: {edit.object_type} has no link '{edit.link_type}'")


def _known_type(session: Session, api_name: str, where: str, hint: str) -> ObjectType:
    object_type = find_object_type(session, api_name)
    if object_type is None:
        raise Invalid(f"{where}: unknown object type '{api_name}'{hint}")
    return object_type


def _show(value: Any) -> str:
    return str(getattr(value, "value", value))
