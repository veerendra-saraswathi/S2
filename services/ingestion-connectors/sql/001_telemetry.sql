-- Telemetry store (TimescaleDB + PostGIS). Applied once when the local `timescaledb`
-- container initialises. Phase 2 moves this under a migration tool.
--
-- Readings are keyed by the natural key (tenant, object type, primary key), not by the
-- ontology's object id, so the ingestion path never has to look anything up and
-- telemetry can arrive before its object is registered.
--
-- The primary keys double as the idempotency guard: delivery from Kafka is
-- at-least-once, and the sink inserts with ON CONFLICT DO NOTHING.

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE SCHEMA IF NOT EXISTS telemetry;

CREATE TABLE telemetry.reading (
    time        timestamptz      NOT NULL,
    tenant_id   uuid             NOT NULL,
    object_type text             NOT NULL,
    object_pk   text             NOT NULL,
    metric      text             NOT NULL,
    value       double precision,           -- numeric and boolean metrics
    value_text  text,                       -- enumerated / textual metrics
    source      text             NOT NULL,
    ingested_at timestamptz      NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, object_type, object_pk, metric, time),
    CHECK (value IS NOT NULL OR value_text IS NOT NULL)
);
SELECT create_hypertable('telemetry.reading', by_range('time', INTERVAL '1 day'));

CREATE TABLE telemetry.position (
    time        timestamptz NOT NULL,
    tenant_id   uuid        NOT NULL,
    object_type text        NOT NULL,
    object_pk   text        NOT NULL,
    geom        geometry(Point, 4326) NOT NULL,
    alt_m       real,
    heading_deg real,
    speed_mps   real,
    source      text        NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, object_type, object_pk, time)
);
SELECT create_hypertable('telemetry.position', by_range('time', INTERVAL '1 day'));
CREATE INDEX position_geom_idx ON telemetry.position USING gist (geom);
