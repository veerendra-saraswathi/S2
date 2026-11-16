"""Executor configuration. Shares the S2_ environment prefix and the common settings."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from s2_common.settings import Settings


class ConnectorConfig(BaseModel):
    """One entry of S2_CONNECTORS, keyed by the connector name used in ontology packs."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["http", "log"] = "log"
    # type=http
    base_url: str | None = None
    bearer_token: str | None = None
    timeout_s: Annotated[float, Field(gt=0)] = 10.0
    # operation name -> path under base_url. An operation not listed here is POSTed to
    # /<operation>.
    paths: dict[str, str] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def _no_trailing_slash(cls, value: str | None) -> str | None:
        return value.rstrip("/") if value else value


class ExecutorSettings(Settings):
    # The executor's own database role: BYPASSRLS (it works across tenants), rights on
    # the outbox and audit log only. Never the application role, never a superuser.
    executor_database_url: str = "postgresql+psycopg://s2_executor:s2_executor_dev@localhost:5432/s2"

    poll_interval_s: Annotated[float, Field(gt=0)] = 1.0
    batch_size: Annotated[int, Field(ge=1, le=500)] = 10
    # How long a claimed row is invisible to other workers. It must exceed the slowest
    # connector timeout, or a slow delivery is retried while still in flight.
    lease_s: Annotated[float, Field(gt=0)] = 60.0
    max_attempts: Annotated[int, Field(ge=1)] = 8
    backoff_base_s: Annotated[float, Field(gt=0)] = 5.0
    backoff_cap_s: Annotated[float, Field(gt=0)] = 900.0

    # JSON object, e.g. {"cmms": {"type": "http", "base_url": "https://cmms.example/api"}}.
    # The default delivers nowhere: the `log` connector records the write-back and
    # reports success, so the local stack shows the whole loop with no external system.
    connectors: dict[str, ConnectorConfig] = Field(
        default_factory=lambda: {
            "samanvaya-fleet": ConnectorConfig(type="log"),
            "cmms": ConnectorConfig(type="log"),
        }
    )


@lru_cache
def get_executor_settings() -> ExecutorSettings:
    return ExecutorSettings()
