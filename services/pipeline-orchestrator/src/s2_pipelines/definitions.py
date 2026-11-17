"""Dagster definitions: the lakehouse datasets and the ontology object types they feed.

    dagster dev -m s2_pipelines.definitions

The asset graph is the lineage: `lakehouse/assets/commercial_drone` -> `ontology/CommercialDrone`,
and each materialisation records the snapshot it read, the column -> property mapping
it applied and what it changed. The schedule ships stopped; turn it on in the UI.
"""

# No `from __future__ import annotations`: Dagster inspects the `context` annotation at runtime.

from typing import Any

import dagster as dg

from s2_pipelines.engine import EngineClient
from s2_pipelines.lakehouse import Lakehouse
from s2_pipelines.settings import PipelineSettings, get_pipeline_settings
from s2_pipelines.sync import Result, Syncer

GROUP = "ontology_sync"


def run_sync(object_type: str, dataset: str, run_id: str) -> Result:
    """The work of one asset. Module-level so tests can replace it."""
    settings = get_pipeline_settings()
    lakehouse = Lakehouse.from_settings(settings)
    syncer = Syncer(lakehouse, EngineClient.from_settings(settings))
    return syncer.sync(
        object_type, expect_dataset=dataset, run_id=run_id, delete_absent=settings.sync_delete_absent
    )


def dataset_key(dataset: str) -> dg.AssetKey:
    return dg.AssetKey(["lakehouse", *dataset.split(".")])


def _sync_asset(object_type: str, dataset: str) -> dg.AssetsDefinition:
    @dg.asset(
        key=dg.AssetKey(["ontology", object_type]),
        deps=[dataset_key(dataset)],
        group_name=GROUP,
        description=f"{object_type} objects, made to agree with {dataset} (main).",
        kinds={"iceberg", "s2"},
    )
    def sync_asset(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
        result = run_sync(object_type, dataset, context.run_id)
        summary = result.summary()
        metadata: dict[str, Any] = {
            **{k: v for k, v in summary.items() if k not in ("object_type", "run_id")},
            "column_lineage": dg.MetadataValue.json(result.plan.mapping.lineage()),
        }
        if result.rejected:
            metadata["rejected_rows"] = dg.MetadataValue.json(result.rejected)
            context.log.warning(f"{len(result.rejected)} row(s) rejected: {result.rejected}")
        return dg.MaterializeResult(metadata=metadata)

    return sync_asset


def build(settings: PipelineSettings) -> dg.Definitions:
    sources = [
        dg.AssetSpec(
            dataset_key(dataset),
            group_name="lakehouse",
            description=f"Iceberg table {dataset}, written outside Dagster (sources, s2-pipelines CLI).",
            kinds={"iceberg"},
        )
        for dataset in sorted(set(settings.sync_targets.values()))
    ]
    syncs = [_sync_asset(object_type, dataset) for object_type, dataset in settings.sync_targets.items()]
    job = dg.define_asset_job("ontology_sync", selection=dg.AssetSelection.groups(GROUP))
    schedule = dg.ScheduleDefinition(
        job=job, cron_schedule=settings.sync_cron, default_status=dg.DefaultScheduleStatus.STOPPED
    )
    return dg.Definitions(assets=[*sources, *syncs], jobs=[job], schedules=[schedule])


defs = build(get_pipeline_settings())
