"""Dataset -> ontology through the real engine API: sync, idempotence, provenance, branches."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from s2_pipelines import demo
from s2_pipelines.engine import EngineClient
from s2_pipelines.lakehouse import Lakehouse
from s2_pipelines.sync import Syncer

Headers = Callable[..., dict[str, str]]


def drone(engine: TestClient, headers: Headers, key: str) -> dict[str, Any]:
    response = engine.get(f"/v1/objects/CommercialDrone/{key}", headers=headers("viewer", "marking:internal"))
    assert response.status_code == 200, response.text
    return response.json()


def audit(engine: TestClient, headers: Headers, **filters: str) -> list[dict[str, Any]]:
    return engine.get("/v1/audit", params=filters, headers=headers("auditor")).json()


def test_sync_patches_the_register_columns_and_nothing_else(
    engine: TestClient, engine_client: EngineClient, lakehouse: Lakehouse, headers: Headers, fleet: None
) -> None:
    # An operator's state change, which the sync must not undo.
    admin = headers("ontology-admin", "marking:internal")
    engine.patch(
        "/v1/objects/CommercialDrone/DRN-0003", json={"properties": {"status": "GROUNDED"}}, headers=admin
    )

    snapshot = lakehouse.write(demo.DATASET, demo.register())
    syncer = Syncer(lakehouse, engine_client)
    result = syncer.sync("CommercialDrone", run_id="run-1")

    assert result.summary()["updated"] == 3 and result.rejected == {}
    after = drone(engine, headers, "DRN-0003")["properties"]
    assert after["serialNumber"] == "GX8-24-0003"
    assert after["firmwareVersion"] == "4.1.0"
    assert after["status"] == "GROUNDED", "operational state is not the dataset's to change"

    entries = audit(engine, headers, event_type="object.modified", resource_id="DRN-0003")
    provenance = entries[0]["details"]["provenance"]
    assert provenance == {
        "dataset": "assets.commercial_drone",
        "snapshot_id": str(snapshot),
        "branch": "main",
        "run_id": "run-1",
    }


def test_a_second_sync_writes_nothing(
    engine: TestClient, engine_client: EngineClient, lakehouse: Lakehouse, headers: Headers, fleet: None
) -> None:
    lakehouse.write(demo.DATASET, demo.register())
    syncer = Syncer(lakehouse, engine_client)
    syncer.sync("CommercialDrone")
    before = len(audit(engine, headers))
    again = syncer.sync("CommercialDrone")
    assert again.summary()["unchanged"] == 3 and again.updated == 0
    assert len(audit(engine, headers)) == before, "no audit noise from a no-op sync"
    assert drone(engine, headers, "DRN-0001")["version"] == 2


def test_a_branch_previews_then_promotes(
    engine: TestClient, engine_client: EngineClient, lakehouse: Lakehouse, headers: Headers, fleet: None
) -> None:
    lakehouse.write(demo.DATASET, demo.register())
    syncer = Syncer(lakehouse, engine_client)
    syncer.sync("CommercialDrone")

    lakehouse.create_branch(demo.DATASET, "preview")
    lakehouse.write(demo.DATASET, demo.firmware_rollout("4.2.0"), branch="preview")

    preview = syncer.plan("CommercialDrone", branch="preview")
    assert preview.updates == {"DRN-0003": {"firmwareVersion": "4.2.0"}}
    assert drone(engine, headers, "DRN-0003")["properties"]["firmwareVersion"] == "4.1.0", (
        "a plan writes nothing"
    )
    assert syncer.plan("CommercialDrone").updates == {}, "main is untouched by the branch"

    promoted = lakehouse.promote(demo.DATASET, "preview")
    result = syncer.sync("CommercialDrone")
    assert result.updated == 1
    assert drone(engine, headers, "DRN-0003")["properties"]["firmwareVersion"] == "4.2.0"
    (latest,) = audit(engine, headers, event_type="object.modified", resource_id="DRN-0003")[:1]
    assert latest["details"]["provenance"]["snapshot_id"] == str(promoted)


def test_new_rows_and_absent_objects(
    engine: TestClient, engine_client: EngineClient, lakehouse: Lakehouse, headers: Headers, fleet: None
) -> None:
    rows = demo.register(
        [*demo.REGISTER[:2], ("DRN-0009", "Garuda-9", "X8-Inspector", "GX8-24-0009", "4.1.0")]
    )
    lakehouse.write(demo.DATASET, rows)
    result = Syncer(lakehouse, engine_client).sync("CommercialDrone")
    assert result.plan.absent == ["DRN-0003"], "kept, reported"
    assert "required status" in result.rejected["DRN-0009"]
    assert engine.get("/v1/objects/CommercialDrone/DRN-0009", headers=headers("viewer")).status_code == 404
    assert drone(engine, headers, "DRN-0003")["properties"]["callSign"] == "Garuda-3"


def test_the_pipeline_is_governed_like_any_client(
    engine: TestClient, lakehouse: Lakehouse, headers: Headers, token: Callable[..., str], fleet: None
) -> None:
    """An identity without the role writes nothing; the rows come back rejected, with the reason."""
    lakehouse.write(demo.DATASET, demo.register())
    weak = EngineClient(engine, lambda: token("viewer"))
    result = Syncer(lakehouse, weak).sync("CommercialDrone")
    assert result.updated == 0
    assert set(result.rejected) == {"DRN-0001", "DRN-0002", "DRN-0003"}
    assert "ontology-admin" in result.rejected["DRN-0001"]


def test_a_create_never_replaces_an_object_made_meanwhile(
    engine: TestClient, engine_client: EngineClient, lakehouse: Lakehouse, headers: Headers, fleet: None
) -> None:
    """Between plan and apply someone creates the object: the sync's create is refused (409),
    it does not overwrite what they wrote."""
    from s2_pipelines.sync import Mapping

    syncer = Syncer(lakehouse, engine_client)
    lakehouse.write(demo.DATASET, demo.register([("DRN-0042", "Late", "X", None, None)]))
    the_plan = syncer.plan("CommercialDrone")
    relaxed = Mapping(**{**the_plan.mapping.__dict__, "required": frozenset({"callSign"})})
    the_plan.mapping = relaxed
    the_plan.creates = {"DRN-0042": {"callSign": "Late", "model": "X"}}

    admin = headers("ontology-admin", "marking:internal")
    body = {"properties": {"callSign": "Theirs", "status": "READY"}}
    assert engine.put("/v1/objects/CommercialDrone/DRN-0042", json=body, headers=admin).status_code == 200

    result = syncer.apply(the_plan)
    assert result.created == 0 and "already exists" in result.rejected["DRN-0042"]
    assert drone(engine, headers, "DRN-0042")["properties"]["callSign"] == "Theirs"


def test_the_catalog_lives_in_postgres_as_the_app_role(database: dict[str, str], tmp_path: Any) -> None:
    """Migration 0003 gives s2_app a schema to keep the catalog in."""
    from pyiceberg.catalog.sql import SqlCatalog

    uri = database["app"] + "?options=-csearch_path%3Dlakehouse"
    warehouse = f"file://{tmp_path}"
    lakehouse = Lakehouse(SqlCatalog("pg", uri=uri, warehouse=warehouse), warehouse=warehouse)
    snapshot = lakehouse.write(demo.DATASET, demo.register())
    assert lakehouse.read(demo.DATASET).snapshot_id == snapshot

    from sqlalchemy import create_engine, text

    with create_engine(database["owner"]).connect() as connection:
        tables = connection.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'lakehouse'")
        ).scalars()
        assert "iceberg_tables" in set(tables)
