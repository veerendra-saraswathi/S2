"""Validation of object property values against their PropertyType definitions.

Instances keep scalar properties in JSONB, so the type system is enforced here rather
than by column types. Every write path (direct upsert, action edit) goes through
`validate_properties`.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from s2_ontology.db.models import GEO_TYPES, LifecycleStatus, ObjectType, PropertyDataType, PropertyType
from s2_ontology.domain.errors import Invalid

UNSET: Any = object()

_GEOJSON_TYPES = {
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
    "GeometryCollection",
}


def primary_key_property(object_type: ObjectType) -> PropertyType:
    return next(p for p in object_type.properties if p.is_primary_key)


def geo_property(object_type: ObjectType) -> PropertyType | None:
    return next((p for p in object_type.properties if p.data_type in GEO_TYPES), None)


def normalise_primary_key(object_type: ObjectType, raw: Any) -> str:
    """Primary keys are stored as text; integer keys must look like integers."""
    prop = primary_key_property(object_type)
    value = str(raw).strip() if raw is not None else ""
    if not value:
        raise Invalid(f"{object_type.api_name}: primary key must not be empty")
    if prop.data_type == PropertyDataType.INTEGER and not re.fullmatch(r"-?\d+", value):
        raise Invalid(f"{object_type.api_name}.{prop.api_name}: primary key must be an integer")
    return value


def validate_properties(
    object_type: ObjectType, incoming: dict[str, Any], *, partial: bool
) -> tuple[dict[str, Any], Any]:
    """Check `incoming` and split it into (JSONB properties, GeoJSON geometry).

    A value of None means "clear this property". The geometry element is UNSET when the
    geo property was not mentioned, None when it is to be cleared, otherwise a GeoJSON
    dict. With partial=False the result is a complete replacement and required
    properties must be present.
    """
    definitions = {p.api_name: p for p in object_type.properties}
    cleaned: dict[str, Any] = {}
    geometry: Any = UNSET

    for name, value in incoming.items():
        prop = definitions.get(name)
        where = f"{object_type.api_name}.{name}"
        if prop is None:
            raise Invalid(f"{where}: unknown property")
        if prop.status == LifecycleStatus.DEPRECATED:
            raise Invalid(f"{where}: property is deprecated and no longer writable")
        if prop.is_primary_key:
            raise Invalid(f"{where}: the primary key is taken from the URL and cannot be reassigned")
        if prop.data_type == PropertyDataType.TIMESERIES:
            raise Invalid(f"{where}: time-series values are written by ingestion, not through the object API")
        if prop.data_type in GEO_TYPES:
            geometry = None if value is None else _check_geojson(prop, value, where)
            continue
        cleaned[name] = None if value is None else _check_value(prop, value, where)

    if not partial:
        for prop in definitions.values():
            if not prop.is_required or prop.is_primary_key or prop.status == LifecycleStatus.DEPRECATED:
                continue
            missing = (
                geometry in (UNSET, None)
                if prop.data_type in GEO_TYPES
                else cleaned.get(prop.api_name) is None
            )
            if missing:
                raise Invalid(f"{object_type.api_name}.{prop.api_name}: required property is missing")
    return cleaned, geometry


def _check_value(prop: PropertyType, value: Any, where: str) -> Any:
    if prop.is_array:
        if not isinstance(value, list):
            raise Invalid(f"{where}: expected a list")
        return [_check_scalar(prop, item, f"{where}[{index}]") for index, item in enumerate(value)]
    return _check_scalar(prop, value, where)


def _check_scalar(prop: PropertyType, value: Any, where: str) -> Any:
    kind = PropertyDataType(prop.data_type)
    is_number = isinstance(value, int | float) and not isinstance(value, bool)

    if kind in (PropertyDataType.STRING, PropertyDataType.MEDIA_REF):
        if not isinstance(value, str):
            raise Invalid(f"{where}: expected a string")
    elif kind == PropertyDataType.INTEGER:
        if not isinstance(value, int) or isinstance(value, bool):
            raise Invalid(f"{where}: expected an integer")
    elif kind == PropertyDataType.DOUBLE:
        if not is_number:
            raise Invalid(f"{where}: expected a number")
    elif kind == PropertyDataType.DECIMAL:
        if not (is_number or isinstance(value, str)):
            raise Invalid(f"{where}: expected a decimal (number or numeric string)")
        try:
            float(value)
        except ValueError as exc:
            raise Invalid(f"{where}: expected a decimal (number or numeric string)") from exc
    elif kind == PropertyDataType.BOOLEAN:
        if not isinstance(value, bool):
            raise Invalid(f"{where}: expected a boolean")
    elif kind == PropertyDataType.DATE:
        _parse(date.fromisoformat, value, where, "an ISO 8601 date")
    elif kind == PropertyDataType.TIMESTAMP:
        _parse(datetime.fromisoformat, value, where, "an ISO 8601 timestamp")
    # PropertyDataType.JSON accepts any JSON value.

    _check_constraints(prop, value, where, is_number)
    return value


def _parse(parser: Any, value: Any, where: str, expected: str) -> None:
    if not isinstance(value, str):
        raise Invalid(f"{where}: expected {expected}")
    try:
        parser(value)
    except ValueError as exc:
        raise Invalid(f"{where}: expected {expected}") from exc


def _check_constraints(prop: PropertyType, value: Any, where: str, is_number: bool) -> None:
    constraints = prop.constraints or {}
    allowed = constraints.get("enum")
    if allowed is not None and value not in allowed:
        raise Invalid(f"{where}: must be one of {allowed}")
    if is_number:
        minimum, maximum = constraints.get("minimum"), constraints.get("maximum")
        if minimum is not None and value < minimum:
            raise Invalid(f"{where}: must be >= {minimum}")
        if maximum is not None and value > maximum:
            raise Invalid(f"{where}: must be <= {maximum}")
    pattern = constraints.get("pattern")
    if pattern is not None and isinstance(value, str) and not re.fullmatch(pattern, value):
        raise Invalid(f"{where}: does not match pattern {pattern}")


def _check_geojson(prop: PropertyType, value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("type") not in _GEOJSON_TYPES:
        raise Invalid(f"{where}: expected a GeoJSON geometry object")
    if prop.data_type == PropertyDataType.GEOPOINT:
        coordinates = value.get("coordinates")
        numeric = isinstance(coordinates, list) and all(
            isinstance(c, int | float) and not isinstance(c, bool) for c in coordinates
        )
        if value["type"] != "Point" or not numeric or len(coordinates) not in (2, 3):
            raise Invalid(f"{where}: expected a GeoJSON Point with [lon, lat] or [lon, lat, alt]")
        lon, lat = coordinates[0], coordinates[1]
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise Invalid(f"{where}: coordinates are [lon, lat] in WGS 84 degrees")
    elif "coordinates" not in value and value["type"] != "GeometryCollection":
        raise Invalid(f"{where}: GeoJSON geometry is missing coordinates")
    return value
