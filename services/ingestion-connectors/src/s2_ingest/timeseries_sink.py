"""Kafka -> TimescaleDB sink.

Consumes telemetry envelopes in batches and writes them to telemetry.reading and
telemetry.position. Offsets are committed only after the database transaction commits,
so delivery is at-least-once; the inserts are idempotent (ON CONFLICT DO NOTHING on the
natural key), so a replayed batch changes nothing.

Run:  python -m s2_ingest.timeseries_sink
"""

from __future__ import annotations

import logging
import signal
from typing import Any

import psycopg
from confluent_kafka import Consumer, KafkaError, Message

from s2_common.logging import configure_logging
from s2_common.settings import get_settings
from s2_ingest.contracts import TelemetryEnvelope, rows_from_envelope

log = logging.getLogger("s2.ingest.timeseries_sink")

# One statement per table per batch: the rows travel as one array per column and
# unnest turns them back into rows. A statement per row (executemany) cost the
# database a parse, plan and round trip each time, and at 100 drones at 10 Hz,
# about 18,000 rows a second, that saturated TimescaleDB. DO NOTHING also skips a
# row that repeats one earlier in the same batch.
INSERT_READINGS = (
    "INSERT INTO telemetry.reading"
    " (time, tenant_id, object_type, object_pk, metric, value, value_text, source)"
    " SELECT * FROM unnest("
    "%s::timestamptz[], %s::uuid[], %s::text[], %s::text[], %s::text[], %s::float8[], %s::text[], %s::text[])"
    " ON CONFLICT DO NOTHING"
)
INSERT_POSITIONS = (
    "INSERT INTO telemetry.position"
    " (time, tenant_id, object_type, object_pk, geom, alt_m, heading_deg, speed_mps, source)"
    " SELECT t, tenant, kind, pk, ST_SetSRID(ST_MakePoint(lon, lat), 4326), alt, heading, speed, src"
    " FROM unnest(%s::timestamptz[], %s::uuid[], %s::text[], %s::text[], %s::float8[], %s::float8[],"
    " %s::float8[], %s::float8[], %s::float8[], %s::text[])"
    " AS u(t, tenant, kind, pk, lon, lat, alt, heading, speed, src)"
    " ON CONFLICT DO NOTHING"
)


def columns(rows: list[tuple[Any, ...]]) -> list[list[Any]]:
    """Rows to one list per column, the shape unnest takes."""
    return [list(column) for column in zip(*rows, strict=True)]


def libpq_url(sqlalchemy_url: str) -> str:
    """Settings carry SQLAlchemy-style URLs; psycopg wants a plain libpq one."""
    scheme, _, rest = sqlalchemy_url.partition("://")
    return f"{scheme.split('+', 1)[0]}://{rest}"


def parse(value: bytes | None) -> TelemetryEnvelope | None:
    if value is None:
        return None
    try:
        return TelemetryEnvelope.model_validate_json(value)
    except ValueError as exc:
        # The bridge validates before producing, so this means another producer wrote
        # to the topic. Skip it rather than block the partition.
        log.error("skipping malformed envelope: %s", exc)
        return None


def next_batch(consumer: Any, size: int, wait_s: float) -> tuple[list[TelemetryEnvelope], int]:
    """Up to `size` envelopes, waiting at most `wait_s` for them. Returns (envelopes, consumed)."""
    envelopes: list[TelemetryEnvelope] = []
    consumed = 0
    message: Message
    for message in consumer.consume(num_messages=size, timeout=wait_s):
        error = message.error()
        if error is not None:
            # The topic appears once the bridge produces its first message.
            if error.code() != KafkaError.UNKNOWN_TOPIC_OR_PART:
                log.error("consumer error: %s", error)
            continue
        consumed += 1
        envelope = parse(message.value())
        if envelope is not None:
            envelopes.append(envelope)
    return envelopes, consumed


class Sink:
    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self._connection = connection

    def write(self, envelopes: list[TelemetryEnvelope]) -> tuple[int, int]:
        """Write a batch in one transaction. Returns (reading rows, position rows) sent."""
        readings: list[tuple[Any, ...]] = []
        positions: list[tuple[Any, ...]] = []
        for envelope in envelopes:
            reading_rows, position_rows = rows_from_envelope(envelope)
            readings.extend(reading_rows)
            positions.extend(position_rows)
        try:
            with self._connection.cursor() as cursor:
                if readings:
                    cursor.execute(INSERT_READINGS, columns(readings))
                if positions:
                    cursor.execute(INSERT_POSITIONS, columns(positions))
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return len(readings), len(positions)


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if not settings.telemetry_database_url:
        raise SystemExit("S2_TELEMETRY_DATABASE_URL is not set")

    consumer = Consumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap,
            "group.id": "s2-timeseries-sink",
            "client.id": "s2-timeseries-sink",
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
        }
    )
    consumer.subscribe([settings.telemetry_topic])
    running = True

    def stop(*_: Any) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    with psycopg.connect(libpq_url(settings.telemetry_database_url)) as connection:
        sink = Sink(connection)
        log.info(
            "consuming %s from %s in batches of up to %d or %.2f s",
            settings.telemetry_topic,
            settings.kafka_bootstrap,
            settings.sink_batch_size,
            settings.sink_batch_wait_s,
        )
        try:
            while running:
                envelopes, consumed = next_batch(
                    consumer, settings.sink_batch_size, settings.sink_batch_wait_s
                )
                if envelopes:
                    readings, positions = sink.write(envelopes)
                    log.debug("wrote %d readings, %d positions", readings, positions)
                if consumed:
                    # Only after the database commit: a crash before this line replays
                    # the batch, and the inserts are idempotent.
                    consumer.commit(asynchronous=False)
        finally:
            consumer.close()


if __name__ == "__main__":
    run()
