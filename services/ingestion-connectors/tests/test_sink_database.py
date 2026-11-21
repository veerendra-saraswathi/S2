"""The sink's writes against a real TimescaleDB, created from sql/001_telemetry.sql.

    S2_TEST_TELEMETRY_URL   libpq URL of a superuser on a TimescaleDB + PostGIS server, e.g.
                            postgresql://postgres:postgres_dev@localhost:5433/postgres
                            (the Compose stack's `timescaledb` works). Unset: these tests skip.

Each run creates its own database and drops it afterwards.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest

from s2_ingest.contracts import TelemetryEnvelope, envelope_from_mqtt
from s2_ingest.timeseries_sink import Sink

SCHEMA = Path(__file__).resolve().parents[1] / "sql" / "001_telemetry.sql"
TENANT = "00000000-0000-0000-0000-000000000001"
T0 = datetime(2026, 11, 21, 6, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def connection() -> Iterator[psycopg.Connection[Any]]:
    url = os.environ.get("S2_TEST_TELEMETRY_URL")
    if not url:
        pytest.skip("set S2_TEST_TELEMETRY_URL to run the sink's database tests")
    name = f"s2_sink_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    test_url = psycopg.conninfo.make_conninfo(url, dbname=name)
    try:
        with psycopg.connect(test_url) as conn:
            conn.execute(SCHEMA.read_text())
            conn.commit()
            yield conn
    finally:
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def envelope(key: str, at: datetime, metrics: dict[str, Any], position: dict[str, Any] | None = None):
    topic = f"s2/v1/{TENANT}/CommercialDrone/{key}/telemetry"
    payload: dict[str, Any] = {"ts": at.isoformat(), "metrics": metrics}
    if position is not None:
        payload["position"] = position
    return envelope_from_mqtt(topic, json.dumps(payload).encode(), source="test")


def readings(conn: psycopg.Connection[Any], key: str) -> list[tuple[Any, ...]]:
    return conn.execute(
        "SELECT time, metric, value, value_text, source FROM telemetry.reading"
        " WHERE object_pk = %s ORDER BY time, metric",
        [key],
    ).fetchall()


def test_a_batch_lands_with_every_value_and_type(connection: psycopg.Connection[Any]) -> None:
    key = f"SINK-{uuid.uuid4().hex[:6]}"
    batch = [
        envelope(
            key,
            T0,
            {"battery_voltage": 24.5, "armed": True, "system_state": "ACTIVE"},
            {"lat": 17.44, "lon": 78.35, "alt_m": 580.5, "heading_deg": 87.0, "speed_mps": 5.0},
        ),
        envelope(key, T0 + timedelta(milliseconds=100), {"battery_voltage": 24.4, "armed": False}),
    ]
    assert Sink(connection).write(batch) == (5, 1)

    assert readings(connection, key) == [
        (T0, "armed", 1.0, None, "test"),
        (T0, "battery_voltage", 24.5, None, "test"),
        (T0, "system_state", None, "ACTIVE", "test"),
        (T0 + timedelta(milliseconds=100), "armed", 0.0, None, "test"),
        (T0 + timedelta(milliseconds=100), "battery_voltage", 24.4, None, "test"),
    ]
    lon, lat, alt, heading, speed = connection.execute(
        "SELECT ST_X(geom), ST_Y(geom), alt_m, heading_deg, speed_mps FROM telemetry.position"
        " WHERE object_pk = %s",
        [key],
    ).fetchone()
    assert (round(lon, 6), round(lat, 6), alt, heading, speed) == (78.35, 17.44, 580.5, 87.0, 5.0)
    srid = connection.execute(
        "SELECT ST_SRID(geom) FROM telemetry.position WHERE object_pk = %s", [key]
    ).fetchone()
    assert srid == (4326,)


def test_a_replayed_batch_changes_nothing(connection: psycopg.Connection[Any]) -> None:
    key = f"SINK-{uuid.uuid4().hex[:6]}"
    batch = [envelope(key, T0, {"rpm": 4200}, {"lat": 1.0, "lon": 2.0})]
    sink = Sink(connection)
    sink.write(batch)
    before = readings(connection, key)
    sink.write(batch)  # Kafka redelivers after a crash before the offset commit
    assert readings(connection, key) == before
    count = connection.execute(
        "SELECT count(*) FROM telemetry.position WHERE object_pk = %s", [key]
    ).fetchone()
    assert count == (1,)


def test_a_repeat_inside_one_batch_is_kept_once(connection: psycopg.Connection[Any]) -> None:
    key = f"SINK-{uuid.uuid4().hex[:6]}"
    one = envelope(key, T0, {"rpm": 4200})
    Sink(connection).write([one, one])
    assert readings(connection, key) == [(T0, "rpm", 4200.0, None, "test")]


def test_a_large_batch_is_one_statement_per_table(connection: psycopg.Connection[Any]) -> None:
    key = f"SINK-{uuid.uuid4().hex[:6]}"
    batch: list[TelemetryEnvelope] = [
        envelope(
            key,
            T0 + timedelta(milliseconds=100 * i),
            {f"m{j}": float(i * j) for j in range(20)},
            {"lat": 17.0, "lon": 78.0},
        )
        for i in range(500)
    ]
    executed: list[str] = []
    real = psycopg.Cursor.execute

    def counting(self, query, params=None, **kwargs):  # type: ignore[no-untyped-def]
        executed.append(str(query)[:30])
        return real(self, query, params, **kwargs)

    psycopg.Cursor.execute = counting  # type: ignore[method-assign]
    try:
        assert Sink(connection).write(batch) == (10_000, 500)
    finally:
        psycopg.Cursor.execute = real  # type: ignore[method-assign]
    assert len(executed) == 2, executed
    count = connection.execute(
        "SELECT count(*) FROM telemetry.reading WHERE object_pk = %s", [key]
    ).fetchone()
    assert count == (10_000,)
