"""Object instances and the links between them.

Storage: scalar properties in a JSONB column (without nulls, without the primary key,
which has its own column) and the one spatial property in a PostGIS geometry column,
written and read as GeoJSON. Callers see a single property map: `to_out` puts the
primary key and the geometry back under their property names.

Every write returns a change record ({"op", "object", "before", "after"}) that the
caller puts in the audit entry.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from s2_common.auth import Principal
from s2_ontology.api.schemas import ObjectOut
from s2_ontology.db.models import (
    Cardinality,
    LifecycleStatus,
    LinkInstance,
    LinkType,
    ObjectInstance,
    ObjectType,
    PropertyDataType,
)
from s2_ontology.domain import registry
from s2_ontology.domain.errors import Conflict, Invalid, NotFound
from s2_ontology.domain.properties import (
    UNSET,
    geo_property,
    normalise_primary_key,
    primary_key_property,
    validate_properties,
)

Found = tuple[ObjectInstance, dict[str, Any] | None]  # (row, geometry as GeoJSON)
WriteMode = Literal["create", "merge", "replace"]

_GEOJSON = func.ST_AsGeoJSON(ObjectInstance.geometry)


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


def _found(row: Any) -> Found:
    instance, geojson = row
    return instance, json.loads(geojson) if geojson else None


def find(session: Session, object_type: ObjectType, key: Any, *, lock: bool = False) -> Found | None:
    query = select(ObjectInstance, _GEOJSON).where(
        ObjectInstance.object_type_id == object_type.id, ObjectInstance.primary_key == str(key)
    )
    if lock:
        query = query.with_for_update()
    row = session.execute(query).first()
    return _found(row) if row else None


def get(session: Session, object_type: ObjectType, key: Any, *, lock: bool = False) -> Found:
    found = find(session, object_type, key, lock=lock)
    if found is None:
        raise NotFound(f"{object_type.api_name} '{key}' does not exist")
    return found


def list_objects(
    session: Session, object_type: ObjectType, *, limit: int = 100, after: str | None = None
) -> list[Found]:
    query = select(ObjectInstance, _GEOJSON).where(ObjectInstance.object_type_id == object_type.id)
    if after is not None:
        query = query.where(ObjectInstance.primary_key > after)
    rows = session.execute(query.order_by(ObjectInstance.primary_key).limit(limit))
    return [_found(row) for row in rows]


def property_map(
    object_type: ObjectType, instance: ObjectInstance, geometry: dict[str, Any] | None
) -> dict[str, Any]:
    """The object's properties as callers see them: primary key first, geometry included."""
    key_property = primary_key_property(object_type)
    key: Any = instance.primary_key
    if key_property.data_type == PropertyDataType.INTEGER:
        key = int(key)
    properties = {key_property.api_name: key, **instance.properties}
    spatial = geo_property(object_type)
    if spatial is not None and geometry is not None:
        properties[spatial.api_name] = geometry
    return properties


def to_out(
    object_type: ObjectType,
    instance: ObjectInstance,
    geometry: dict[str, Any] | None,
    hidden: frozenset[str] = frozenset(),
) -> ObjectOut:
    """`hidden`: properties the caller may not see (domain/markings.py); left out."""
    properties = property_map(object_type, instance, geometry)
    return ObjectOut(
        object_type=object_type.api_name,
        primary_key=instance.primary_key,
        properties={name: value for name, value in properties.items() if name not in hidden},
        version=instance.version,
        created_at=instance.created_at,
        updated_at=instance.updated_at,
    )


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #


def _label(object_type: ObjectType, key: str) -> str:
    return f"{object_type.api_name}/{key}"


def _diff(before: dict[str, Any], after: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    changed = [name for name in before.keys() | after.keys() if before.get(name) != after.get(name)]
    ordered = sorted(changed)
    return {name: before.get(name) for name in ordered}, {name: after.get(name) for name in ordered}


def _geometry_sql(geometry: dict[str, Any] | None) -> Any:
    if geometry is None:
        return None
    return func.ST_SetSRID(func.ST_GeomFromGeoJSON(json.dumps(geometry)), 4326)


def write(
    session: Session,
    principal: Principal,
    object_type: ObjectType,
    key: Any,
    values: dict[str, Any],
    *,
    mode: WriteMode,
    preserve: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Create or change one object.

    create   the object must not exist; `values` is its complete state
    merge    the object must exist; `values` overlays it, None clears a property
    replace  upsert; `values` is the complete state, anything left out is cleared,
             except the properties named in `preserve`, which keep their current
             values (a caller replacing an object it cannot fully see)
    """
    if preserve & values.keys():
        raise ValueError("a preserved property cannot also be written")
    if object_type.status != LifecycleStatus.ACTIVE:
        raise Conflict(f"object type '{object_type.api_name}' is {object_type.status} and cannot be written")
    key = normalise_primary_key(object_type, key)
    label = _label(object_type, key)
    existing = find(session, object_type, key, lock=True)

    if mode == "create" and existing is not None:
        raise Conflict(f"{label} already exists")
    if mode == "merge" and existing is None:
        raise NotFound(f"{label} does not exist")
    if mode == "replace" and existing is not None and preserve:
        current = property_map(object_type, *existing)
        values = {**{name: current[name] for name in preserve if name in current}, **values}

    cleaned, geometry = validate_properties(object_type, values, partial=mode == "merge")
    key_name = primary_key_property(object_type).api_name

    if existing is None:
        stored = {name: value for name, value in cleaned.items() if value is not None}
        instance = ObjectInstance(
            tenant_id=principal.tenant_id,
            created_by=principal.subject,
            object_type_id=object_type.id,
            primary_key=key,
            properties=stored,
        )
        new_geometry = None if geometry is UNSET else geometry
        if new_geometry is not None:
            instance.geometry = _geometry_sql(new_geometry)
        session.add(instance)
        session.flush()
        after = property_map(object_type, instance, new_geometry)
        after.pop(key_name)
        return {"op": "create_object", "object": label, "before": None, "after": after}

    instance, old_geometry = existing
    before = property_map(object_type, instance, old_geometry)

    if mode == "merge":
        merged = {**instance.properties, **cleaned}
        stored = {name: value for name, value in merged.items() if value is not None}
        new_geometry = old_geometry if geometry is UNSET else geometry
        _require_present(object_type, stored, new_geometry)
    else:
        stored = {name: value for name, value in cleaned.items() if value is not None}
        new_geometry = None if geometry is UNSET else geometry

    if stored != instance.properties:
        instance.properties = stored
    if new_geometry != old_geometry:
        instance.geometry = _geometry_sql(new_geometry)
    session.flush()

    after = property_map(object_type, instance, new_geometry)
    changed_before, changed_after = _diff(before, after)
    op = "modify_object" if mode == "merge" else "replace_object"
    return {"op": op, "object": label, "before": changed_before, "after": changed_after}


def _require_present(
    object_type: ObjectType, stored: dict[str, Any], geometry: dict[str, Any] | None
) -> None:
    """A merge may not clear a required property."""
    spatial = geo_property(object_type)
    for prop in object_type.properties:
        if not prop.is_required or prop.is_primary_key or prop.status == LifecycleStatus.DEPRECATED:
            continue
        present = geometry is not None if prop is spatial else prop.api_name in stored
        if not present:
            raise Invalid(f"{object_type.api_name}.{prop.api_name}: required property is missing")


def delete(session: Session, object_type: ObjectType, key: Any) -> dict[str, Any]:
    key = normalise_primary_key(object_type, key)
    instance, geometry = get(session, object_type, key, lock=True)
    before = property_map(object_type, instance, geometry)
    before.pop(primary_key_property(object_type).api_name)
    session.delete(instance)  # its links go with it (ON DELETE CASCADE)
    session.flush()
    return {"op": "delete_object", "object": _label(object_type, key), "before": before, "after": None}


# --------------------------------------------------------------------------- #
# Links
# --------------------------------------------------------------------------- #


def resolve_link(session: Session, object_type: ObjectType, name: str) -> tuple[LinkType, bool]:
    """Find a link by its forward or reverse name as seen from `object_type`.

    Returns (link type, True if `name` is the reverse name).
    """
    forward = registry.find_link_type(session, object_type, name)
    if forward is not None:
        return forward, False
    reverse = session.scalar(
        select(LinkType).where(
            LinkType.target_object_type_id == object_type.id, LinkType.reverse_api_name == name
        )
    )
    if reverse is not None:
        return reverse, True
    raise NotFound(f"{object_type.api_name} has no link '{name}'")


def _forward(session: Session, object_type: ObjectType, name: str) -> tuple[LinkType, ObjectType]:
    link_type, reverse = resolve_link(session, object_type, name)
    if reverse:
        source = link_type.source_object_type.api_name
        raise Invalid(f"'{name}' is a reverse name; write the link from {source} as '{link_type.api_name}'")
    return link_type, registry.get_object_type(session, link_type.target_object_type.api_name)


def _ends(
    session: Session, object_type: ObjectType, key: Any, link_name: str, far_key: Any
) -> tuple[LinkType, ObjectInstance, ObjectInstance, dict[str, Any]]:
    """Resolve and lock both ends of a link. The row locks make the cardinality check safe."""
    link_type, far_type = _forward(session, object_type, link_name)
    key = normalise_primary_key(object_type, key)
    far_key = normalise_primary_key(far_type, far_key)
    source, _ = get(session, object_type, key, lock=True)
    target, _ = get(session, far_type, far_key, lock=True)
    record = {"object": _label(object_type, key), "link": link_name, "target": _label(far_type, far_key)}
    return link_type, source, target, record


def _count(session: Session, link_type: LinkType, **ends: Any) -> int:
    query = select(func.count()).select_from(LinkInstance).where(LinkInstance.link_type_id == link_type.id)
    for column, value in ends.items():
        query = query.where(getattr(LinkInstance, column) == value)
    return session.scalar(query) or 0


def create_link(
    session: Session, principal: Principal, object_type: ObjectType, key: Any, link_name: str, far_key: Any
) -> dict[str, Any]:
    """Link two objects. Linking a pair that is already linked changes nothing."""
    link_type, source, target, record = _ends(session, object_type, key, link_name, far_key)
    record = {"op": "create_link", **record}
    if _count(session, link_type, source_object_id=source.id, target_object_id=target.id):
        return {**record, "changed": False}

    cardinality = link_type.cardinality
    if cardinality in (Cardinality.ONE_TO_ONE, Cardinality.MANY_TO_ONE) and _count(
        session, link_type, source_object_id=source.id
    ):
        raise Conflict(f"'{link_name}' is {cardinality.value}: {record['object']} is already linked")
    if cardinality in (Cardinality.ONE_TO_ONE, Cardinality.ONE_TO_MANY) and _count(
        session, link_type, target_object_id=target.id
    ):
        raise Conflict(f"'{link_name}' is {cardinality.value}: {record['target']} is already linked")

    session.add(
        LinkInstance(
            tenant_id=principal.tenant_id,
            created_by=principal.subject,
            link_type_id=link_type.id,
            source_object_id=source.id,
            target_object_id=target.id,
        )
    )
    session.flush()
    return {**record, "changed": True}


def delete_link(
    session: Session, object_type: ObjectType, key: Any, link_name: str, far_key: Any
) -> dict[str, Any]:
    link_type, source, target, record = _ends(session, object_type, key, link_name, far_key)
    link = session.scalar(
        select(LinkInstance).where(
            LinkInstance.link_type_id == link_type.id,
            LinkInstance.source_object_id == source.id,
            LinkInstance.target_object_id == target.id,
        )
    )
    if link is not None:
        session.delete(link)
        session.flush()
    return {"op": "delete_link", **record, "changed": link is not None}


def linked(
    session: Session, object_type: ObjectType, key: Any, link_name: str, *, limit: int = 500
) -> tuple[ObjectType, list[Found]]:
    """Follow a link by its forward or reverse name. Returns (type at the far end, objects)."""
    link_type, reverse = resolve_link(session, object_type, link_name)
    instance, _ = get(session, object_type, key)
    if reverse:
        near, far = LinkInstance.target_object_id, LinkInstance.source_object_id
        far_type = link_type.source_object_type
    else:
        near, far = LinkInstance.source_object_id, LinkInstance.target_object_id
        far_type = link_type.target_object_type
    query = (
        select(ObjectInstance, _GEOJSON)
        .join(LinkInstance, far == ObjectInstance.id)
        .where(LinkInstance.link_type_id == link_type.id, near == instance.id)
        .order_by(ObjectInstance.primary_key)
        .limit(limit)
    )
    far_type = registry.get_object_type(session, far_type.api_name)  # with its properties loaded
    return far_type, [_found(row) for row in session.execute(query)]
