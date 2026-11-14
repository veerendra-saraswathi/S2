"""Pack validation and the small pure pieces: no database needed."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from s2_ontology.api.schemas import OntologyPack
from s2_ontology.db.models import ObjectType, PropertyType
from s2_ontology.domain.errors import Invalid
from s2_ontology.domain.properties import UNSET, validate_properties
from s2_ontology.domain.templates import TemplateError, referenced_params, render

from .conftest import load_pack


@pytest.mark.parametrize("name", ["core-fleet", "energy-grid"])
def test_shipped_packs_are_valid(name: str) -> None:
    pack = OntologyPack.model_validate(load_pack(name))
    assert pack.name == name
    assert pack.object_types


def _mutated(mutate: Any) -> dict[str, Any]:
    pack = copy.deepcopy(load_pack("core-fleet"))
    mutate(pack)
    return pack


def _drone(pack: dict[str, Any]) -> dict[str, Any]:
    return pack["object_types"][0]


def _prop(pack: dict[str, Any], name: str) -> dict[str, Any]:
    return next(p for p in _drone(pack)["properties"] if p["api_name"] == name)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda p: _prop(p, "callSign").update(is_primary_key=True),
            "exactly one property must be the primary key",
        ),
        (lambda p: _prop(p, "droneId").update(data_type="double"), "scalar string or integer"),
        (lambda p: _prop(p, "batteryVoltage").pop("binding"), "needs binding.metric"),
        (lambda p: _prop(p, "model").update(is_title=True), "at most one property may be the title"),
        (lambda p: _prop(p, "model").update(data_type="geoshape"), "at most one geopoint/geoshape"),
        (lambda p: _prop(p, "position").update(is_array=True), "cannot be an array"),
        (lambda p: _drone(p).update(api_name="commercial_drone"), "String should match pattern"),
        (lambda p: _prop(p, "model").update(api_name="Model"), "String should match pattern"),
        (lambda p: _drone(p).update(colour="red"), "Extra inputs are not permitted"),
        (lambda p: _drone(p)["properties"].append(dict(_prop(p, "model"))), "duplicate property"),
        (lambda p: p["action_types"][1].update(requires_approval=False), "must require human approval"),
        (
            lambda p: p["action_types"][0].update(policy_path="../../v1/policies"),
            "String should match pattern",
        ),
        (
            lambda p: p["action_types"][0]["edits"][0].update(op="create_link"),
            "requires link_type and link_target",
        ),
        (lambda p: p["action_types"][0].update(edits=[]), "at least 1 item"),
    ],
)
def test_invalid_packs_are_rejected(mutate: Any, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        OntologyPack.model_validate(_mutated(mutate))


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #


def test_render_keeps_the_type_of_a_whole_value_reference() -> None:
    params = {"count": 3, "flag": False, "name": "DRN-1"}
    assert render("{{ params.count }}", params) == 3
    assert render("{{params.flag}}", params) is False
    assert render({"id": "{{ params.name }}", "nested": ["{{ params.count }}"]}, params) == {
        "id": "DRN-1",
        "nested": [3],
    }


def test_render_interpolates_inside_text_and_requires_the_parameter() -> None:
    assert render("drone {{ params.name }} grounded", {"name": "DRN-1"}) == "drone DRN-1 grounded"
    assert render("{{ params.missing }}", {}) is None
    with pytest.raises(TemplateError, match="missing"):
        render("note: {{ params.missing }}", {})


def test_render_is_not_an_expression_language() -> None:
    assert render("{{ 1 + 1 }}", {}) == "{{ 1 + 1 }}"
    assert render("{{ params.a.__class__ }}", {"a": 1}) == "{{ params.a.__class__ }}"


def test_referenced_params() -> None:
    value = {"a": "{{ params.x }}", "b": ["{{ params.y }} and {{ params.x }}", 5], "c": None}
    assert referenced_params(value) == {"x", "y"}


# --------------------------------------------------------------------------- #
# Property validation
# --------------------------------------------------------------------------- #


def _object_type() -> ObjectType:
    pack = OntologyPack.model_validate(load_pack("energy-grid"))
    spec = next(o for o in pack.object_types if o.api_name == "Substation")
    object_type = ObjectType(api_name=spec.api_name, display_name=spec.display_name, status="active")
    object_type.properties = [PropertyType(status="active", **p.model_dump()) for p in spec.properties]
    return object_type


def test_valid_properties_split_into_jsonb_and_geometry() -> None:
    point = {"type": "Point", "coordinates": [78.35, 17.44]}
    cleaned, geometry = validate_properties(
        _object_type(),
        {"name": "Gachibowli", "voltageKv": 220, "location": point, "thermalScans": ["s3://s2-media/a.tiff"]},
        partial=False,
    )
    assert cleaned == {"name": "Gachibowli", "voltageKv": 220, "thermalScans": ["s3://s2-media/a.tiff"]}
    assert geometry == point


def test_partial_update_leaves_geometry_unset() -> None:
    cleaned, geometry = validate_properties(_object_type(), {"voltageKv": None}, partial=True)
    assert cleaned == {"voltageKv": None}
    assert geometry is UNSET


@pytest.mark.parametrize(
    ("properties", "message"),
    [
        ({"name": "x", "colour": "red"}, "unknown property"),
        ({"name": "x", "substationId": "SS-2"}, "primary key"),
        ({"name": 5}, "expected a string"),
        ({"name": "x", "voltageKv": "220"}, "expected an integer"),
        ({"name": "x", "voltageKv": True}, "expected an integer"),
        ({"name": "x", "voltageKv": 0}, "must be >= 1"),
        ({"name": "x", "thermalScans": "s3://one"}, "expected a list"),
        ({"name": "x", "location": {"type": "Point", "coordinates": [200, 17]}}, "WGS 84"),
        ({"name": "x", "location": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}}, "GeoJSON Point"),
        ({"voltageKv": 220}, "required property is missing"),
    ],
)
def test_invalid_properties_are_rejected(properties: dict[str, Any], message: str) -> None:
    with pytest.raises(Invalid, match=message):
        validate_properties(_object_type(), properties, partial=False)
