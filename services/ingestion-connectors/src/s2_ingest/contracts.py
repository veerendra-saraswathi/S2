"""The telemetry contract between the field (Samanvaya 0.5 / 1) and S2.

PLACEHOLDER CONTRACT, version 1. It is S2's own definition, written before the real
Samanvaya 0.5 topic tree and payloads were available. Aligning with the fleet side
means changing this one module: the topic parser, `TelemetryMessage`, and the mapping
in `rows_from_envelope`. Everything downstream consumes the envelope.

MQTT topic    s2/v1/{tenant_id}/{object_type}/{object_pk}/telemetry
MQTT payload  {
                "ts": "2026-11-14T17:00:00.123Z",          RFC 3339, required
                "metrics": {"battery_voltage": 22.4},       name -> number | string | bool
                "position": {"lat": 17.44, "lon": 78.35, "alt_m": 120.5,
                             "heading_deg": 87.0, "speed_mps": 12.1},   optional
                "seq": 1234                                 optional, per-source counter
              }

Kafka         topic s2.telemetry.raw, key "{tenant_id}/{object_type}/{object_pk}" so one
              object's readings stay ordered; value is a TelemetryEnvelope as JSON.
              Messages that fail validation go to s2.telemetry.dlq with the reason.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

TOPIC_FILTER = "s2/v1/+/+/+/telemetry"
_TOPIC = re.compile(
    r"^s2/v1/(?P<tenant>[^/]+)/(?P<object_type>[A-Z][A-Za-z0-9]{0,62})/(?P<object_pk>[^/]+)/telemetry$"
)
_METRIC_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class ContractError(ValueError):
    """The message does not conform to the telemetry contract."""


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    alt_m: float | None = None
    heading_deg: float | None = Field(default=None, ge=0, lt=360)
    speed_mps: float | None = Field(default=None, ge=0)


class TelemetryMessage(BaseModel):
    """The MQTT payload."""

    model_config = ConfigDict(extra="forbid")

    ts: AwareDatetime
    metrics: dict[str, float | bool | str] = Field(default_factory=dict)
    position: Position | None = None
    seq: int | None = Field(default=None, ge=0)


class TelemetryEnvelope(BaseModel):
    """What travels on Kafka: the payload plus where it came from."""

    tenant_id: uuid.UUID
    object_type: str
    object_pk: str
    source: str
    received_at: AwareDatetime
    message: TelemetryMessage

    @property
    def key(self) -> str:
        return f"{self.tenant_id}/{self.object_type}/{self.object_pk}"


def parse_topic(topic: str) -> tuple[uuid.UUID, str, str]:
    match = _TOPIC.fullmatch(topic)
    if match is None:
        raise ContractError(f"topic does not match s2/v1/<tenant>/<ObjectType>/<key>/telemetry: {topic}")
    try:
        tenant = uuid.UUID(match["tenant"])
    except ValueError as exc:
        raise ContractError(f"tenant in topic is not a UUID: {match['tenant']}") from exc
    return tenant, match["object_type"], match["object_pk"]


def envelope_from_mqtt(topic: str, payload: bytes, *, source: str = "mqtt") -> TelemetryEnvelope:
    tenant, object_type, object_pk = parse_topic(topic)
    try:
        message = TelemetryMessage.model_validate_json(payload)
    except ValueError as exc:
        raise ContractError(f"invalid payload: {exc}") from exc
    bad = [name for name in message.metrics if not _METRIC_NAME.fullmatch(name)]
    if bad:
        raise ContractError(f"metric names must be snake_case: {bad}")
    if message.position is None and not message.metrics:
        raise ContractError("message carries neither metrics nor a position")
    return TelemetryEnvelope(
        tenant_id=tenant,
        object_type=object_type,
        object_pk=object_pk,
        source=source,
        received_at=datetime.now(UTC),
        message=message,
    )


ReadingRow = tuple[datetime, uuid.UUID, str, str, str, float | None, str | None, str]
PositionRow = tuple[
    datetime, uuid.UUID, str, str, float, float, float | None, float | None, float | None, str
]


def rows_from_envelope(envelope: TelemetryEnvelope) -> tuple[list[ReadingRow], list[PositionRow]]:
    """Flatten one envelope into rows for telemetry.reading and telemetry.position."""
    message = envelope.message
    identity: tuple[Any, ...] = (message.ts, envelope.tenant_id, envelope.object_type, envelope.object_pk)
    readings: list[ReadingRow] = []
    for metric, value in sorted(message.metrics.items()):
        if isinstance(value, bool):
            number, label = (1.0 if value else 0.0), None
        elif isinstance(value, int | float):
            number, label = float(value), None
        else:
            number, label = None, value
        readings.append((*identity, metric, number, label, envelope.source))
    positions: list[PositionRow] = []
    if message.position is not None:
        p = message.position
        positions.append((*identity, p.lon, p.lat, p.alt_m, p.heading_deg, p.speed_mps, envelope.source))
    return readings, positions
