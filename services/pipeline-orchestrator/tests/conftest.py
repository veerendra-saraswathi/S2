"""Fixtures for the pipeline tests.

* `lakehouse`: a real Iceberg catalog (PyIceberg's SQL catalog on SQLite) with a warehouse
  in a temporary directory. No server needed.
* `engine` / `engine_client`: the real ontology engine, in-process, on a real PostgreSQL
  migrated with its own revisions, called through its HTTP API exactly as the pipeline
  calls it in production. Needs S2_TEST_ADMIN_URL (as the engine's own tests do);
  without it those tests skip.
* S2_TEST_S3_ENDPOINT (e.g. http://127.0.0.1:8333, a SeaweedFS with the dev keys): also
  run the lakehouse tests against S3. Unset: skipped.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import jwt
import pytest
import yaml
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from pyiceberg.catalog.sql import SqlCatalog
from sqlalchemy import create_engine, make_url, text

from s2_common.auth import TokenVerifier
from s2_common.opa import OpaClient
from s2_common.settings import Settings
from s2_ontology.app import create_app
from s2_pipelines.engine import EngineClient
from s2_pipelines.lakehouse import Lakehouse

REPO_ROOT = Path(__file__).resolve().parents[3]
ENGINE_ROOT = REPO_ROOT / "services" / "ontology-engine"
ISSUER = "https://idp.test/realms/s2"
AUDIENCE = "s2-api"


@pytest.fixture
def lakehouse(tmp_path: Path) -> Lakehouse:
    warehouse = f"file://{tmp_path / 'warehouse'}"
    catalog = SqlCatalog("test", uri=f"sqlite:///{tmp_path / 'catalog.db'}", warehouse=warehouse)
    return Lakehouse(catalog, warehouse=warehouse)


# --------------------------------------------------------------------------- the engine


@pytest.fixture(scope="session")
def database() -> Iterator[dict[str, str]]:
    admin_url = os.environ.get("S2_TEST_ADMIN_URL")
    if not admin_url:
        pytest.skip("set S2_TEST_ADMIN_URL to run the end-to-end tests")
    app_password = os.environ.get("S2_TEST_APP_PASSWORD", "s2_app_dev")
    name = f"s2_pipe_test_{uuid.uuid4().hex[:10]}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
        if not connection.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = 's2_app'")):
            connection.execute(text(f"CREATE ROLE s2_app LOGIN PASSWORD '{app_password}'"))
    owner_url = make_url(admin_url).set(database=name)
    config = Config(str(ENGINE_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ENGINE_ROOT / "migrations"))
    owner_dsn = owner_url.render_as_string(hide_password=False)
    config.set_main_option("sqlalchemy.url", owner_dsn.replace("%", "%%"))
    command.upgrade(config, "head")
    app_url = owner_url.set(username="s2_app", password=app_password)
    try:
        yield {"owner": owner_dsn, "app": app_url.render_as_string(hide_password=False)}
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(scope="session")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def engine(database: dict[str, str], signing_key: rsa.RSAPrivateKey) -> Iterator[TestClient]:
    verifier = TokenVerifier(
        issuer=ISSUER, audience=AUDIENCE, key_resolver=lambda _t: signing_key.public_key()
    )
    dead_opa = "http://127.0.0.1:9"  # the object routes never consult policy
    settings = Settings(
        database_url=database["app"],
        telemetry_database_url=None,
        opa_url=dead_opa,
        oidc_issuer=ISSUER,
        oidc_audience=AUDIENCE,
    )
    app = create_app(settings, verifier=verifier, opa=OpaClient(dead_opa, timeout_s=0.2))
    with TestClient(app) as client:
        yield client


Token = Callable[..., str]


@pytest.fixture
def tenant() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def token(signing_key: rsa.RSAPrivateKey, tenant: uuid.UUID) -> Token:
    def _make(*roles: str, **extra: Any) -> str:
        claims: dict[str, Any] = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": "pipeline-" + "-".join(sorted(roles)),
            "exp": 4102444800,
            "tenant_id": str(tenant),
            "realm_access": {"roles": list(roles)},
            **extra,
        }
        return jwt.encode(claims, signing_key, algorithm="RS256")

    return _make


PIPELINE_ROLES = ("ontology-admin", "marking:internal")


@pytest.fixture
def engine_client(engine: TestClient, token: Token) -> EngineClient:
    return EngineClient(engine, lambda: token(*PIPELINE_ROLES))


@pytest.fixture
def headers(token: Token) -> Callable[..., dict[str, str]]:
    return lambda *roles: {"Authorization": f"Bearer {token(*roles)}"}


@pytest.fixture
def fleet(engine: TestClient, headers: Callable[..., dict[str, str]]) -> None:
    """The core-fleet pack and the demo drones, as `make seed` leaves them."""
    pack = yaml.safe_load((REPO_ROOT / "ontologies" / "core-fleet" / "pack.yaml").read_text())
    admin = headers(*PIPELINE_ROLES)
    assert engine.post("/v1/ontology/packs:apply", json=pack, headers=admin).status_code == 200
    for key, call_sign in (("DRN-0001", "Garuda-1"), ("DRN-0002", "Garuda-2"), ("DRN-0003", "Garuda-3")):
        body = {"properties": {"callSign": call_sign, "model": "X8-Inspector", "status": "READY"}}
        assert engine.put(f"/v1/objects/CommercialDrone/{key}", json=body, headers=admin).status_code == 200
