"""Environment-driven configuration shared by every S2 service.

All variables are prefixed `S2_`. The defaults match the local Docker Compose stack as
seen from the host, so a service started with `uv run` on your machine talks to the
stack without any extra configuration.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="S2_", env_file=".env", extra="ignore")

    env: str = "dev"
    log_level: str = "INFO"

    # Ontology store. The application role is a non-owner, so row-level security applies.
    database_url: str = "postgresql+psycopg://s2_app:s2_app_dev@localhost:5432/s2"
    # Telemetry store (TimescaleDB). Optional: without it, time-series reads return 503.
    telemetry_database_url: str | None = None

    # Identity. Tokens are validated against the issuer the browser sees; signing keys
    # are fetched from wherever the service can reach Keycloak.
    oidc_issuer: str = "http://localhost:8080/realms/s2"
    oidc_jwks_url: str = "http://localhost:8080/realms/s2/protocol/openid-connect/certs"
    oidc_token_url: str = "http://localhost:8080/realms/s2/protocol/openid-connect/token"
    oidc_audience: str = "s2-api"
    oidc_cli_client_id: str = "s2-cli"

    # Policy
    opa_url: str = "http://localhost:8181"
    opa_timeout_s: float = 2.0

    # Messaging
    kafka_bootstrap: str = "localhost:29092"
    mqtt_url: str = "mqtt://localhost:1883"
    # The broker requires a login. The password is read from a file (a Compose secret),
    # never from the URL or an environment variable, so it stays out of `ps` and logs.
    # mqtts:// URLs verify the broker against mqtt_ca_file, or the system roots if unset.
    mqtt_username: str | None = None
    mqtt_password_file: str | None = None
    mqtt_ca_file: str | None = None
    telemetry_topic: str = "s2.telemetry.raw"
    telemetry_dlq_topic: str = "s2.telemetry.dlq"
    # The telemetry sink writes a batch when it has this many messages or has waited
    # this long, whichever comes first. The wait bounds the latency it adds when
    # traffic is light; under load batches fill before it expires.
    sink_batch_size: int = 500
    sink_batch_wait_s: float = 0.1


@lru_cache
def get_settings() -> Settings:
    return Settings()
