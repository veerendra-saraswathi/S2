"""The agent against the real engine (in-process, over HTTP), real OPA and a real telemetry
table, with a scripted stand-in for the model. Needs S2_TEST_ADMIN_URL and `opa` on PATH
(or S2_TEST_OPA_URL); without them the end-to-end tests skip."""

from __future__ import annotations

import datetime as dt
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
from sqlalchemy import create_engine, make_url, text

from s2_agents.engine import Engine
from s2_common.auth import TokenVerifier
from s2_common.opa import OpaClient
from s2_common.settings import Settings
from s2_ontology.app import create_app

REPO = Path(__file__).resolve().parents[3]
ENGINE_ROOT = REPO / "services" / "ontology-engine"
ISSUER = "https://idp.test/realms/s2"
AUDIENCE = "s2-api"


@pytest.fixture(scope="session")
def database() -> Iterator[dict[str, str]]:
    admin_url = os.environ.get("S2_TEST_ADMIN_URL")
    if not admin_url:
        pytest.skip("set S2_TEST_ADMIN_URL to run the end-to-end tests")
    name = f"s2_agent_test_{uuid.uuid4().hex[:10]}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
        if not connection.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = 's2_app'")):
            connection.execute(text("CREATE ROLE s2_app LOGIN PASSWORD 's2_app_dev'"))
    owner = make_url(admin_url).set(database=name)
    config = Config(str(ENGINE_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ENGINE_ROOT / "migrations"))
    owner_dsn = owner.render_as_string(hide_password=False)
    config.set_main_option("sqlalchemy.url", owner_dsn.replace("%", "%%"))
    command.upgrade(config, "head")
    with create_engine(owner_dsn).begin() as connection:
        connection.execute(text("CREATE SCHEMA telemetry"))
        connection.execute(
            text(
                "CREATE TABLE telemetry.reading (time timestamptz NOT NULL, tenant_id uuid NOT NULL, "
                "object_type text NOT NULL, object_pk text NOT NULL, metric text NOT NULL, "
                "value double precision, value_text text, source text NOT NULL DEFAULT 'test', "
                "PRIMARY KEY (tenant_id, object_type, object_pk, metric, time))"
            )
        )
    app_dsn = owner.set(username="s2_app", password="s2_app_dev").render_as_string(hide_password=False)
    try:
        yield {"owner": owner_dsn, "app": app_dsn}
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(scope="session")
def opa_url() -> Iterator[str]:
    if external := os.environ.get("S2_TEST_OPA_URL"):
        yield external.rstrip("/")
        return
    binary = shutil.which("opa")
    if binary is None:
        pytest.skip("install opa or set S2_TEST_OPA_URL")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [binary, "run", "--server", f"--addr=127.0.0.1:{port}", str(REPO / "policies")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(75):
            try:
                if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        yield url
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.fixture(scope="session")
def key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def engine(database: dict[str, str], opa_url: str, key: rsa.RSAPrivateKey) -> Iterator[TestClient]:
    verifier = TokenVerifier(issuer=ISSUER, audience=AUDIENCE, key_resolver=lambda _t: key.public_key())
    settings = Settings(
        database_url=database["app"],
        telemetry_database_url=database["owner"],
        opa_url=opa_url,
        oidc_issuer=ISSUER,
        oidc_audience=AUDIENCE,
    )
    with TestClient(create_app(settings, verifier=verifier, opa=OpaClient(opa_url))) as client:
        yield client


@pytest.fixture
def tenant() -> uuid.UUID:
    return uuid.uuid4()


Token = Callable[..., str]


@pytest.fixture
def token(key: rsa.RSAPrivateKey, tenant: uuid.UUID) -> Token:
    def make(*roles: str, sub: str = "someone", **extra: Any) -> str:
        claims = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": sub,
            "exp": 4102444800,
            "tenant_id": str(tenant),
            "realm_access": {"roles": list(roles)},
            **extra,
        }
        return jwt.encode(claims, key, algorithm="RS256")

    return make


def headers_for(token: Token, *roles: str, **extra: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(*roles, **extra)}"}


AGENT_ROLES = ("operator", "viewer", "marking:internal")


@pytest.fixture
def agent_engine(engine: TestClient, token: Token) -> Engine:
    """The engine as the s2-agent service account sees it."""
    return Engine(
        engine,
        lambda: token(*AGENT_ROLES, sub="svc-s2-agent", s2_actor_type="agent", s2_on_behalf_of="supervisor"),
    )


@pytest.fixture
def fleet(engine: TestClient, token: Token, database: dict[str, str], tenant: uuid.UUID) -> None:
    """core-fleet and energy-grid; D1 in service with failing actuators, D2 healthy, D3 grounded."""
    admin = headers_for(token, "ontology-admin", "marking:internal")
    for name in ("core-fleet", "energy-grid"):
        pack = yaml.safe_load((REPO / "ontologies" / name / "pack.yaml").read_text())
        assert engine.post("/v1/ontology/packs:apply", json=pack, headers=admin).status_code == 200
    for key, status in (("D1", "READY"), ("D2", "READY"), ("D3", "GROUNDED")):
        body = {"properties": {"callSign": f"Garuda-{key}", "status": status}}
        assert engine.put(f"/v1/objects/CommercialDrone/{key}", json=body, headers=admin).status_code == 200
    now = dt.datetime.now(dt.UTC)
    rows = []
    for minute in range(30, -1, -1):
        t = now - dt.timedelta(minutes=minute)
        rows.append((t, "D1", "actuator_health", 0.95 - 0.009 * (30 - minute)))  # 0.95 -> 0.68
        rows.append((t, "D2", "actuator_health", 0.97))
        rows.append((t, "D3", "actuator_health", 0.55))
    with create_engine(database["owner"]).begin() as connection:
        connection.execute(
            text(
                "INSERT INTO telemetry.reading (time, tenant_id, object_type, object_pk, metric, value) "
                "VALUES (:t, :tenant, 'CommercialDrone', :k, :m, :v)"
            ),
            [{"t": t, "tenant": tenant, "k": k, "m": m, "v": v} for t, k, m, v in rows],
        )


class Scripted:
    """A stand-in for the model: each reply is a function of the conversation so far."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.conversations: list[list[dict[str, Any]]] = []
        self.tools: list[list[dict[str, Any]]] = []

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        self.conversations.append([dict(m) for m in messages])
        self.tools.append(tools)
        # Past the script (another queue's item), it dismisses.
        reply = self.replies.pop(0) if self.replies else call("dismiss", reason="nothing to add")
        if callable(reply):
            reply = reply(messages)
        if isinstance(reply, Exception):
            raise reply
        return {
            "role": "assistant",
            "content": reply.get("content", ""),
            "tool_calls": reply.get("tool_calls", []),
        }


def call(name: str, **arguments: Any) -> dict[str, Any]:
    return {"tool_calls": [{"function": {"name": name, "arguments": arguments}}]}
