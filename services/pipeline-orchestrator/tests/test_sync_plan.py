"""Planning a sync: pure, no catalog and no engine."""

from __future__ import annotations

from typing import Any

import pytest

from s2_pipelines.lakehouse import DatasetVersion
from s2_pipelines.sync import Mapping, SyncError, plan


def prop(name: str, column: str | None = None, **flags: Any) -> dict[str, Any]:
    return {
        "api_name": name,
        "source_column": column,
        "is_primary_key": flags.get("pk", False),
        "is_required": flags.get("required", False) or flags.get("pk", False),
        "data_type": flags.get("data_type", "string"),
        "status": flags.get("status", "active"),
    }


DEFINITION = {
    "api_name": "CommercialDrone",
    "backing_dataset": "assets.commercial_drone",
    "properties": [
        prop("droneId", "drone_id", pk=True),
        prop("callSign", "call_sign", required=True),
        prop("model", "model"),
        prop("status", required=True),  # owned by operations: not in the dataset
        prop("oldName", "old_name", status="deprecated"),
        prop("battery", "battery_v", data_type="timeseries"),
    ],
}
MAPPING = Mapping.from_definition(DEFINITION)
COLUMNS = ["drone_id", "call_sign", "model"]


def version(*rows: tuple[Any, ...], columns: list[str] = COLUMNS) -> DatasetVersion:
    return DatasetVersion(
        "assets.commercial_drone", "main", 42, [dict(zip(columns, r, strict=True)) for r in rows], columns
    )


def test_the_mapping_comes_from_the_definition() -> None:
    assert MAPPING.key_property == "droneId" and MAPPING.key_column == "drone_id"
    assert MAPPING.columns == {"callSign": "call_sign", "model": "model"}, "no deprecated, no series, no key"
    assert MAPPING.required == {"callSign", "status"}
    assert MAPPING.lineage() == {"drone_id": "droneId", "call_sign": "callSign", "model": "model"}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"backing_dataset": None}, "no backing_dataset"),
        (
            {"properties": [prop("droneId", pk=True), prop("model", "model")]},
            "primary key has no source_column",
        ),
        (
            {"properties": [prop("droneId", "drone_id", pk=True), prop("model")]},
            "no property has a source_column",
        ),
    ],
)
def test_an_unmappable_type_is_refused(change: dict[str, Any], message: str) -> None:
    with pytest.raises(SyncError, match=message):
        Mapping.from_definition({**DEFINITION, **change})


def test_updates_touch_only_mapped_changed_values() -> None:
    existing = {
        "D1": {"droneId": "D1", "callSign": "G1", "model": "X8", "status": "GROUNDED"},
        "D2": {"droneId": "D2", "callSign": "G2", "model": "X8", "status": "READY"},
    }
    result = plan(MAPPING, version(("D1", "G1", "X9"), ("D2", "G2", "X8")), existing)
    assert result.updates == {"D1": {"model": "X9"}}, "status is never written"
    assert result.unchanged == 1
    assert result.creates == {} and result.rejected == {} and result.absent == []


def test_a_null_clears_a_mapped_value() -> None:
    existing = {"D1": {"droneId": "D1", "callSign": "G1", "model": "X8", "status": "READY"}}
    assert plan(MAPPING, version(("D1", "G1", None)), existing).updates == {"D1": {"model": None}}
    unset = {"D1": {"droneId": "D1", "callSign": "G1", "status": "READY"}}
    assert plan(MAPPING, version(("D1", "G1", None)), unset).unchanged == 1, "null and absent are the same"


def test_creating_needs_every_required_property() -> None:
    result = plan(MAPPING, version(("D9", "G9", "X8")), {})
    assert result.creates == {}
    assert "required status" in result.rejected["D9"]

    relaxed = Mapping(**{**MAPPING.__dict__, "required": frozenset({"callSign"})})
    assert plan(relaxed, version(("D9", "G9", None)), {}).creates == {"D9": {"callSign": "G9"}}


def test_bad_keys_are_rejected_not_fatal() -> None:
    existing = {"D1": {"callSign": "G1", "model": "X8"}}
    result = plan(
        MAPPING,
        version((None, "G", "X"), ("D1", "G1", "X9"), ("D1", "G1", "X8"), ("D2", "G2", "X")),
        existing,
    )
    assert result.rejected["row 0"] == "drone_id is empty"
    assert "more than once" in result.rejected["D1"]
    assert "D1" not in result.updates, "a duplicated key is not written at all"


def test_objects_without_a_row_are_absent_not_deleted() -> None:
    existing = {"D1": {"callSign": "G1", "model": "X8"}, "D7": {"callSign": "G7"}}
    assert plan(MAPPING, version(("D1", "G1", "X8")), existing).absent == ["D7"]


def test_a_missing_column_stops_the_sync() -> None:
    with pytest.raises(SyncError, match="no column"):
        plan(MAPPING, version(("D1", "G1"), columns=["drone_id", "call_sign"]), {})


def test_keys_are_compared_as_strings() -> None:
    integer = Mapping(**{**MAPPING.__dict__})
    result = plan(integer, version((17, "G", "X")), {"17": {"callSign": "G", "model": "X"}})
    assert result.unchanged == 1
