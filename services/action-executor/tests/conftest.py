"""Fixtures for the relay tests.

The relay tests need a real PostgreSQL (the claim query, row-level security, grants and
the audit-chain trigger are the point), migrated with the ontology engine's own Alembic
revisions, so the executor is tested against the schema it will meet in production.

    S2_TEST_ADMIN_URL   SQLAlchemy URL of a superuser on a PostgreSQL 18 + PostGIS server,
                        as for the ontology engine's tests. Unset: database tests skip.

Each test gets its own tenant.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, make_url, text

from s2_executor.main import libpq_url
from s2_executor.settings import ExecutorSettings

REPO_ROOT = Path(__file__).resolve().parents[3]
# Overridable so the migrations can be run from a patched copy on a server without PostGIS.
ENGINE_ROOT = Path(os.environ.get("S2_TEST_ENGINE_ROOT", REPO_ROOT / "services" / "ontology-engine"))
EXECUTOR_PASSWORD = "s2_executor_test"


@pytest.fixture(scope="session")
def database() -> Iterator[dict[str, str]]:
    admin_url = os.environ.get("S2_TEST_ADMIN_URL")
    if not admin_url:
        pytest.skip("set S2_TEST_ADMIN_URL to run the database tests")
    app_password = os.environ.get("S2_TEST_APP_PASSWORD", "s2_app_dev")
    name = f"s2_exec_test_{uuid.uuid4().hex[:10]}"

    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
        if not connection.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = 's2_app'")):
            connection.execute(text(f"CREATE ROLE s2_app LOGIN PASSWORD '{app_password}'"))

    owner_url = make_url(admin_url).set(database=name)
    with create_engine(owner_url, isolation_level="AUTOCOMMIT").connect() as connection:
        # PostgreSQL 18 has uuidv7(); an older server (a laptop's) gets a stand-in.
        connection.execute(
            text(
                "DO $$ BEGIN IF to_regproc('uuidv7') IS NULL THEN "
                "CREATE FUNCTION public.uuidv7() RETURNS uuid LANGUAGE sql AS "
                "'SELECT gen_random_uuid()'; END IF; END $$"
            )
        )

    config = Config(str(ENGINE_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ENGINE_ROOT / "migrations"))
    owner_dsn = owner_url.render_as_string(hide_password=False)
    config.set_main_option("sqlalchemy.url", owner_dsn.replace("%", "%%"))
    command.upgrade(config, "head")

    with admin.connect() as connection:
        connection.execute(text(f"ALTER ROLE s2_executor LOGIN PASSWORD '{EXECUTOR_PASSWORD}'"))
    urls = {
        "owner": owner_url.render_as_string(hide_password=False),
        "executor": owner_url.set(username="s2_executor", password=EXECUTOR_PASSWORD).render_as_string(
            hide_password=False
        ),
        "app": owner_url.set(username="s2_app", password=app_password).render_as_string(hide_password=False),
    }
    try:
        yield urls
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def owner(database: dict[str, str], empty_outbox: None) -> Iterator[psycopg.Connection[Any]]:
    """Superuser connection: seeds data and inspects results, bypassing every restriction."""
    with psycopg.connect(libpq_url(database["owner"]), autocommit=True) as connection:
        yield connection


@pytest.fixture
def empty_outbox(database: dict[str, str]) -> None:
    """The relay serves every tenant, so rows left by one test would be claimed by the next."""
    with psycopg.connect(libpq_url(database["owner"]), autocommit=True) as connection:
        connection.execute("DELETE FROM ontology.writeback_outbox")


@pytest.fixture
def executor_db(database: dict[str, str], empty_outbox: None) -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(libpq_url(database["executor"]), autocommit=True) as connection:
        yield connection


@pytest.fixture
def connect_executor(
    database: dict[str, str], empty_outbox: None
) -> Iterator[Callable[[], psycopg.Connection[Any]]]:
    """For tests that need several independent executor connections (workers)."""
    opened: list[psycopg.Connection[Any]] = []

    def _connect() -> psycopg.Connection[Any]:
        connection = psycopg.connect(libpq_url(database["executor"]), autocommit=True)
        opened.append(connection)
        return connection

    yield _connect
    for connection in opened:
        connection.close()


@pytest.fixture
def settings() -> ExecutorSettings:
    return ExecutorSettings(batch_size=10, lease_s=60, max_attempts=3, backoff_base_s=10, backoff_cap_s=100)


@pytest.fixture
def tenant() -> uuid.UUID:
    return uuid.uuid4()


Seed = Callable[..., list[uuid.UUID]]


@pytest.fixture
def seed(owner: psycopg.Connection[Any], tenant: uuid.UUID) -> Seed:
    """seed(n, connector=..., operation=..., tenant_id=...) -> outbox ids, oldest first."""

    def _seed(
        count: int = 1,
        *,
        connector: str = "cmms",
        operation: str = "create_work_order",
        tenant_id: uuid.UUID | None = None,
        status: str = "pending",
        due_in_s: float = 0,
    ) -> list[uuid.UUID]:
        tid = tenant_id or tenant
        action_type_id = owner.execute(
            "INSERT INTO ontology.action_type (api_name, display_name, tenant_id, created_by)"
            " VALUES ('someAction', 'Some action', %s, 'test')"
            " ON CONFLICT (tenant_id, api_name) DO UPDATE SET display_name = EXCLUDED.display_name"
            " RETURNING id",
            (tid,),
        ).fetchone()[0]
        execution_id = owner.execute(
            "INSERT INTO ontology.action_execution"
            " (action_type_id, action_type_version, status, actor_type, actor_id, idempotency_key, tenant_id)"
            " VALUES (%s, 1, 'succeeded', 'user', 'tester', %s, %s) RETURNING id",
            (action_type_id, str(uuid.uuid4()), tid),
        ).fetchone()[0]
        ids = []
        for index in range(count):
            row = owner.execute(
                "INSERT INTO ontology.writeback_outbox"
                " (action_execution_id, connector, operation, payload, status, next_attempt_at, tenant_id)"
                " VALUES (%s, %s, %s, %s::jsonb, %s, now() + make_interval(secs => %s), %s) RETURNING id",
                (
                    execution_id,
                    connector,
                    operation,
                    f'{{"external_id": "WO-{index}", "title": "secret title"}}',
                    status,
                    due_in_s,
                    tid,
                ),
            ).fetchone()
            ids.append(row[0])
        return ids

    return _seed
