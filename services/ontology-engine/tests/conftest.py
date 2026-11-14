"""Test fixtures.

The API tests are integration tests: a real PostgreSQL (migrated with Alembic, queried
as the non-owner application role so row-level security is in force) and a real OPA
serving the repository's policies. Only the identity provider is replaced, by tokens
signed with a throwaway key.

    S2_TEST_ADMIN_URL   SQLAlchemy URL of a superuser on a PostgreSQL 18 + PostGIS
                        server, e.g. postgresql+psycopg://postgres:postgres_dev@localhost:5432/postgres
                        (the Compose stack's `postgres` works). Unset: database tests skip.
    S2_TEST_APP_PASSWORD  password of the s2_app role on that server (default s2_app_dev).
    S2_TEST_OPA_URL     an already-running OPA. Unset: `opa` is started from PATH,
                        and if it is not installed the policy tests skip.

Each test gets its own tenant, so tests are isolated from each other by the same
mechanism that isolates customers.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
import yaml
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, make_url, text

from s2_common.auth import TokenVerifier
from s2_common.opa import OpaClient
from s2_common.settings import Settings
from s2_ontology.app import create_app

ENGINE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ENGINE_ROOT.parents[1]
ISSUER = "https://idp.test/realms/s2"
AUDIENCE = "s2-api"


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def verifier(signing_key: rsa.RSAPrivateKey) -> TokenVerifier:
    return TokenVerifier(
        issuer=ISSUER, audience=AUDIENCE, key_resolver=lambda _token: signing_key.public_key()
    )


MakeToken = Callable[..., str]


@pytest.fixture(scope="session")
def make_token(signing_key: rsa.RSAPrivateKey) -> MakeToken:
    def _make(
        *roles: str,
        tenant: uuid.UUID | str | None,
        sub: str | None = None,
        key: Any = None,
        algorithm: str = "RS256",
        **claims: Any,
    ) -> str:
        now = int(time.time())
        payload: dict[str, Any] = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": sub or f"user-{'-'.join(roles) or 'anonymous'}",
            "iat": now,
            "exp": now + 300,
            "preferred_username": sub or (roles[0] if roles else "anonymous"),
            "realm_access": {"roles": list(roles)},
        }
        if tenant is not None:
            payload["tenant_id"] = str(tenant)
        payload.update(claims)
        return jwt.encode(payload, key or signing_key, algorithm=algorithm)

    return _make


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def database() -> Iterator[dict[str, str]]:
    admin_url = os.environ.get("S2_TEST_ADMIN_URL")
    if not admin_url:
        pytest.skip("set S2_TEST_ADMIN_URL to run the database tests")
    app_password = os.environ.get("S2_TEST_APP_PASSWORD", "s2_app_dev")
    name = f"s2_test_{uuid.uuid4().hex[:10]}"

    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
        if not connection.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = 's2_app'")):
            # A fresh server (CI). An existing role, such as the Compose stack's, is left alone.
            connection.execute(text(f"CREATE ROLE s2_app LOGIN PASSWORD '{app_password}'"))

    owner_url = make_url(admin_url).set(database=name)
    app_url = owner_url.set(username="s2_app", password=app_password)
    urls = {
        "owner": owner_url.render_as_string(hide_password=False),
        "app": app_url.render_as_string(hide_password=False),
    }

    config = Config(str(ENGINE_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", urls["owner"].replace("%", "%%"))
    command.upgrade(config, "head")
    try:
        yield urls
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(scope="session")
def owner_engine(database: dict[str, str]) -> Iterator[Engine]:
    """A superuser connection, for tests that play the malicious insider."""
    engine = create_engine(database["owner"])
    yield engine
    engine.dispose()


# --------------------------------------------------------------------------- #
# Policy engine
# --------------------------------------------------------------------------- #


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def opa_url() -> Iterator[str]:
    external = os.environ.get("S2_TEST_OPA_URL")
    if external:
        yield external.rstrip("/")
        return
    binary = shutil.which("opa")
    if binary is None:
        pytest.skip("install the opa binary or set S2_TEST_OPA_URL to run the policy tests")
    url = f"http://127.0.0.1:{_free_port()}"
    process = subprocess.Popen(
        [
            binary,
            "run",
            "--server",
            f"--addr={url.removeprefix('http://')}",
            "--set=decision_logs.console=true",
            str(REPO_ROOT / "policies"),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        else:
            pytest.fail("OPA did not become healthy")
        yield url
    finally:
        process.terminate()
        process.wait(timeout=10)


# --------------------------------------------------------------------------- #
# Application
# --------------------------------------------------------------------------- #


def _settings(database: dict[str, str], opa_url: str) -> Settings:
    return Settings(
        database_url=database["app"],
        telemetry_database_url=None,
        opa_url=opa_url,
        oidc_issuer=ISSUER,
        oidc_audience=AUDIENCE,
    )


@pytest.fixture(scope="session")
def client(database: dict[str, str], opa_url: str, verifier: TokenVerifier) -> Iterator[TestClient]:
    app = create_app(_settings(database, opa_url), verifier=verifier, opa=OpaClient(opa_url))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def client_without_opa(database: dict[str, str], verifier: TokenVerifier) -> Iterator[TestClient]:
    """The same application, but its policy engine is down."""
    dead = f"http://127.0.0.1:{_free_port()}"
    app = create_app(_settings(database, dead), verifier=verifier, opa=OpaClient(dead, timeout_s=0.5))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def tenant() -> uuid.UUID:
    return uuid.uuid4()


Auth = Callable[..., dict[str, str]]


@pytest.fixture
def auth(make_token: MakeToken, tenant: uuid.UUID) -> Auth:
    """`auth("operator")` -> headers for a user holding that role in this test's tenant.
    The subject is derived from the roles, so different role sets are different people."""

    def _auth(*roles: str, sub: str | None = None, tenant_id: uuid.UUID | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer {make_token(*roles, tenant=tenant_id or tenant, sub=sub)}"}

    return _auth


def load_pack(name: str) -> dict[str, Any]:
    return yaml.safe_load((REPO_ROOT / "ontologies" / name / "pack.yaml").read_text())


@pytest.fixture
def ontology(client: TestClient, auth: Auth) -> None:
    """The shipped packs, applied to this test's tenant."""
    for name in ("core-fleet", "energy-grid"):
        response = client.post(
            "/v1/ontology/packs:apply", json=load_pack(name), headers=auth("ontology-admin")
        )
        assert response.status_code == 200, response.text


@pytest.fixture
def drone(client: TestClient, auth: Auth, ontology: None) -> str:
    """A READY drone, DRN-0001."""
    body = {"properties": {"callSign": "Garuda-1", "model": "X8", "status": "READY"}}
    response = client.put("/v1/objects/CommercialDrone/DRN-0001", json=body, headers=auth("ontology-admin"))
    assert response.status_code == 200, response.text
    return "DRN-0001"
