"""The Dagster definitions: the asset graph is the lineage, and a run records what it did."""

from __future__ import annotations

import dagster as dg
import pytest

from s2_pipelines import definitions
from s2_pipelines.lakehouse import DatasetVersion
from s2_pipelines.settings import PipelineSettings
from s2_pipelines.sync import Mapping, Plan, Result

MAPPING = Mapping(
    "CommercialDrone", "assets.commercial_drone", "droneId", "drone_id", {"model": "model"}, frozenset()
)


def fake_result(rejected: dict[str, str] | None = None) -> Result:
    version = DatasetVersion(
        "assets.commercial_drone", "main", 77, [{"drone_id": "D1", "model": "X"}], ["drone_id", "model"]
    )
    plan = Plan(MAPPING, version, updates={"D1": {"model": "X"}})
    return Result(plan, "run", updated=1, rejected=rejected or {})


@pytest.fixture
def defs() -> dg.Definitions:
    return definitions.build(PipelineSettings(sync_targets={"CommercialDrone": "assets.commercial_drone"}))


def test_the_graph_runs_from_dataset_to_object_type(defs: dg.Definitions) -> None:
    graph = defs.resolve_asset_graph()
    sync = dg.AssetKey(["ontology", "CommercialDrone"])
    assert graph.get(sync).parent_keys == {dg.AssetKey(["lakehouse", "assets", "commercial_drone"])}
    schedule = defs.resolve_schedule_def("ontology_sync_schedule")
    assert schedule.default_status == dg.DefaultScheduleStatus.STOPPED


def test_a_run_records_the_snapshot_and_the_column_lineage(
    defs: dg.Definitions, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []

    def run_sync(object_type: str, dataset: str, run_id: str) -> Result:
        calls.append((object_type, dataset))
        return fake_result()

    monkeypatch.setattr(definitions, "run_sync", run_sync)
    result = defs.resolve_job_def("ontology_sync").execute_in_process()
    assert result.success
    assert calls == [("CommercialDrone", "assets.commercial_drone")]
    (materialization,) = result.asset_materializations_for_node(
        result.dagster_run.job_name and "ontology__CommercialDrone"
    )
    metadata = materialization.metadata
    assert metadata["snapshot_id"].value == "77"
    assert metadata["updated"].value == 1
    assert metadata["column_lineage"].value == {"drone_id": "droneId", "model": "model"}


def test_rejected_rows_are_recorded_not_fatal(defs: dg.Definitions, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(definitions, "run_sync", lambda *_: fake_result({"D9": "cannot create"}))
    result = defs.resolve_job_def("ontology_sync").execute_in_process()
    assert result.success
    (materialization,) = result.asset_materializations_for_node("ontology__CommercialDrone")
    assert materialization.metadata["rejected_rows"].value == {"D9": "cannot create"}
