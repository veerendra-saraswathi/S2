"""Datasets, snapshots and branches on a real Iceberg catalog."""

from __future__ import annotations

import datetime as dt
import decimal
import os
import uuid

import pyarrow as pa
import pytest

from s2_pipelines import demo
from s2_pipelines.lakehouse import Lakehouse, LakehouseError, _json_value

DATASET = demo.DATASET


def keyed(lakehouse: Lakehouse, branch: str = "main") -> dict[str, str]:
    return {r["drone_id"]: r["firmware_version"] for r in lakehouse.read(DATASET, branch).rows}


def test_write_then_read_names_the_snapshot(lakehouse: Lakehouse) -> None:
    first = lakehouse.write(DATASET, demo.register())
    version = lakehouse.read(DATASET)
    assert version.snapshot_id == first
    assert version.branch == "main"
    assert version.columns == demo.SCHEMA.names
    assert [r["drone_id"] for r in version.rows] == ["DRN-0001", "DRN-0002", "DRN-0003"]

    second = lakehouse.write(DATASET, demo.firmware_rollout())
    assert second != first
    assert keyed(lakehouse)["DRN-0003"] == "4.2.0"
    old = lakehouse.read(DATASET, snapshot_id=first)
    assert {r["firmware_version"] for r in old.rows} == {"4.1.0"}, "an old snapshot still reads as it was"


def test_a_branch_is_isolated_until_promoted(lakehouse: Lakehouse) -> None:
    base = lakehouse.write(DATASET, demo.register())
    assert lakehouse.create_branch(DATASET, "preview") == base
    lakehouse.write(DATASET, demo.firmware_rollout(), branch="preview")

    assert keyed(lakehouse)["DRN-0003"] == "4.1.0"
    assert keyed(lakehouse, "preview")["DRN-0003"] == "4.2.0"
    assert set(lakehouse.branches(DATASET)) == {"main", "preview"}

    promoted = lakehouse.promote(DATASET, "preview")
    assert promoted == lakehouse.read(DATASET, "preview").snapshot_id
    assert keyed(lakehouse)["DRN-0003"] == "4.2.0"
    assert lakehouse.promote(DATASET, "preview") == promoted, "promoting again is a no-op"


def test_promotion_is_fast_forward_only(lakehouse: Lakehouse) -> None:
    lakehouse.write(DATASET, demo.register())
    lakehouse.create_branch(DATASET, "preview")
    lakehouse.write(DATASET, demo.firmware_rollout("4.2.0"), branch="preview")
    lakehouse.write(DATASET, demo.firmware_rollout("9.9.9", drones=("DRN-0001",)))  # main moves on

    with pytest.raises(LakehouseError, match="main has moved"):
        lakehouse.promote(DATASET, "preview")
    assert keyed(lakehouse)["DRN-0001"] == "9.9.9", "main is untouched"


def test_branch_errors(lakehouse: Lakehouse) -> None:
    with pytest.raises(LakehouseError, match="does not exist"):
        lakehouse.read("assets.nothing")
    lakehouse.write(DATASET, demo.register())
    with pytest.raises(LakehouseError, match="no branch 'ghost'"):
        lakehouse.read(DATASET, "ghost")
    with pytest.raises(LakehouseError, match="create it first"):
        lakehouse.write(DATASET, demo.register(), branch="ghost")
    with pytest.raises(LakehouseError, match="'main'"):
        lakehouse.create_branch(DATASET, "main")
    lakehouse.create_branch(DATASET, "b")
    with pytest.raises(LakehouseError, match="already has"):
        lakehouse.create_branch(DATASET, "b")
    lakehouse.drop_branch(DATASET, "b")
    assert set(lakehouse.branches(DATASET)) == {"main"}
    with pytest.raises(LakehouseError, match="cannot be dropped"):
        lakehouse.drop_branch(DATASET, "main")


def test_an_empty_table_has_no_main(lakehouse: Lakehouse) -> None:
    lakehouse.catalog.create_namespace_if_not_exists("assets")
    lakehouse.catalog.create_table("assets.empty", schema=demo.SCHEMA)
    with pytest.raises(LakehouseError, match="no data yet"):
        lakehouse.read("assets.empty")


def test_values_come_out_as_json() -> None:
    assert _json_value(dt.date(2026, 11, 17)) == "2026-11-17"
    assert _json_value(dt.datetime(2026, 11, 17, 9, 30, tzinfo=dt.UTC)) == "2026-11-17T09:30:00+00:00"
    assert _json_value(decimal.Decimal("1.5")) == 1.5
    assert _json_value([decimal.Decimal("2")]) == [2.0]
    key = uuid.uuid4()
    assert _json_value(key) == str(key)


def test_typed_columns_round_trip(lakehouse: Lakehouse) -> None:
    rows = pa.table(
        {
            "id": pa.array(["a"]),
            "commissioned": pa.array([dt.date(2025, 1, 2)]),
            "cost": pa.array([decimal.Decimal("12.50")], type=pa.decimal128(10, 2)),
        }
    )
    lakehouse.write("assets.typed", rows)
    assert lakehouse.read("assets.typed").rows == [{"id": "a", "commissioned": "2025-01-02", "cost": 12.5}]


@pytest.mark.skipif(not os.environ.get("S2_TEST_S3_ENDPOINT"), reason="set S2_TEST_S3_ENDPOINT")
def test_on_s3(tmp_path) -> None:
    from pyiceberg.catalog.sql import SqlCatalog

    s3 = {
        "s3.endpoint": os.environ["S2_TEST_S3_ENDPOINT"],
        "s3.access-key-id": os.environ.get("S2_TEST_S3_ACCESS_KEY", "s2dev"),
        "s3.secret-access-key": os.environ.get("S2_TEST_S3_SECRET_KEY", "s2devsecret"),
        "s3.region": "us-east-1",
    }
    # One bucket, a fresh prefix per run: like the stack, which has a single lakehouse bucket.
    warehouse = f"s3://s2-lakehouse-test/{uuid.uuid4().hex[:8]}"
    catalog = SqlCatalog("s3", uri=f"sqlite:///{tmp_path / 'c.db'}", warehouse=warehouse, **s3)
    lakehouse = Lakehouse(catalog, warehouse=warehouse, s3=s3)
    lakehouse.ensure_warehouse()
    lakehouse.ensure_warehouse()  # idempotent
    test_a_branch_is_isolated_until_promoted(lakehouse)
