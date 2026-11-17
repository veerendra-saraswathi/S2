"""Executor configuration. Shares the S2_ environment prefix and the common settings."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from s2_common.settings import Settings


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_s: Annotated[float, Field(gt=0)] = 10.0


class LogConfig(_Config):
    """Records the write-back in the log and reports success. The local default."""

    type: Literal["log"] = "log"


class HttpConfig(_Config):
    """A generic JSON webhook: POST <base_url><path> with an Idempotency-Key header."""

    type: Literal["http"] = "http"
    base_url: str | None = None
    bearer_token: str | None = None
    # operation name -> path under base_url. An operation not listed is POSTed to /<operation>.
    paths: dict[str, str] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def _no_trailing_slash(cls, value: str | None) -> str | None:
        return value.rstrip("/") if value else value


# The ontology's work-order priorities, as each CMMS numbers them by default.
DEFAULT_PRIORITIES = {"CRITICAL": "1", "HIGH": "2", "MEDIUM": "3", "LOW": "4"}


class MaximoConfig(_Config):
    """IBM Maximo Manage, through its JSON API (/maximo/api/os/<object structure>)."""

    type: Literal["maximo"]
    base_url: str  # https://maximo.example.com  (the /maximo/api path is added)
    api_key: str
    site_id: str
    object_structure: str = "mxapiwo"
    work_type: str | None = None
    # The work-order attribute that holds S2's work-order id. Looked up before every create,
    # so a retried delivery finds the work order it already made instead of making another.
    reference_field: str = "externalrefid"
    priorities: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_PRIORITIES))

    @field_validator("base_url")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.rstrip("/")


class SapPmConfig(_Config):
    """SAP S/4HANA Plant Maintenance: a maintenance notification through API_MAINTNOTIFICATION."""

    type: Literal["sap-pm"]
    base_url: str  # https://s4.example.com
    service_path: str = "/sap/opu/odata/sap/API_MAINTNOTIFICATION"
    username: str | None = None  # basic authentication (communication user)
    password: str | None = None
    bearer_token: str | None = None  # or an OAuth token
    sap_client: str | None = None  # sent as the sap-client query parameter
    notification_type: str = "M1"
    # A notification field that holds S2's work-order id, if your system has one (often a
    # custom field). With it, a retried delivery finds the notification instead of making a
    # second. Without it, delivery is at-least-once and a retry after a lost response can
    # duplicate; see services/action-executor/README.md.
    reference_field: str | None = None
    priorities: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_PRIORITIES))

    @field_validator("base_url")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.rstrip("/")


ConnectorConfig = Annotated[LogConfig | HttpConfig | MaximoConfig | SapPmConfig, Field(discriminator="type")]


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
            "samanvaya-fleet": LogConfig(),
            "cmms": LogConfig(),
        }
    )


@lru_cache
def get_executor_settings() -> ExecutorSettings:
    return ExecutorSettings()
