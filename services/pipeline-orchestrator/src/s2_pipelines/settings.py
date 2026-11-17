"""Pipeline configuration. Shares the S2_ prefix and the common settings."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field

from s2_common.settings import Settings


class PipelineSettings(Settings):
    # ---- Iceberg catalog (ADR 0011): PyIceberg's SQL catalog in the ontology database,
    # in schema `lakehouse` (migration 0003).
    catalog_uri: str = (
        "postgresql+psycopg://s2_app:s2_app_dev@localhost:5432/s2?options=-csearch_path%3Dlakehouse"
    )
    # Where data and metadata files go: s3://bucket[/prefix] or file:///path.
    warehouse: str = "s3://s2-lakehouse"
    s3_endpoint: str | None = "http://localhost:8333"
    s3_access_key: str | None = "s2dev"
    s3_secret_key: str | None = "s2devsecret"
    s3_region: str = "us-east-1"

    # ---- The ontology engine. The sync writes through its public API, so it is governed
    # like any other client: role checks, markings, validation, audit.
    engine_api_url: str = "http://localhost:8000"
    # Production: a confidential client with a service account (client credentials).
    pipeline_client_id: str | None = None
    pipeline_client_secret: str | None = None
    # Development: a dev user through the CLI client's password grant.
    pipeline_user: str = "admin"
    pipeline_password: str = "admin-dev"

    # ---- What Dagster schedules: object type -> the dataset that backs it. Must match
    # `backing_dataset` in the pack; the sync refuses to run if it does not.
    sync_targets: dict[str, str] = Field(
        default_factory=lambda: {"CommercialDrone": "assets.commercial_drone"}
    )
    sync_cron: str = "*/10 * * * *"


@lru_cache
def get_pipeline_settings() -> PipelineSettings:
    return PipelineSettings()
