"""The telemetry contract and the bridge's forwarding logic. No brokers needed."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

from s2_ingest.contracts import (
    ContractError,
    TelemetryEnvelope,
    envelope_from_mqtt,
    parse_topic,
    rows_from_envelope,
)
from s2_ingest.mqtt_bridge import Bridge
from s2_ingest.timeseries_sink import libpq_url, parse

TENANT = "00000000-0000-0000-0000-000000000001"
TOPIC = f"s2/v1/{TENANT}/CommercialDrone/DRN-0001/telemetry"
PAYLOAD = {
    "ts": "2026-11-14T17:00:00.123Z",
    "seq": 7,
    "metrics": {"battery_voltage": 22.4, "armed": True, "flight_mode": "AUTO"},
    "position": {"lat": 17.44, "lon": 78.35, "alt_m": 120.5, "heading_deg": 87.0, "speed_mps": 12.1},
}


def encode(payload: dict) -> bytes:
    return json.dumps(payload).encode()


def test_topic_is_parsed() -> None:
    assert parse_topic(TOPIC) == (uuid.UUID(TENANT), "CommercialDrone", "DRN-0001")


@pytest.mark.parametrize(
    "topic",
    [
        "s2/v1/not-a-uuid/CommercialDrone/DRN-1/telemetry",
        f"s2/v2/{TENANT}/CommercialDrone/DRN-1/telemetry",
        f"s2/v1/{TENANT}/commercial_drone/DRN-1/telemetry",
        f"s2/v1/{TENANT}/CommercialDrone/DRN-1/status",
        f"s2/v1/{TENANT}/CommercialDrone/a/b/telemetry",
    ],
)
def test_bad_topics_are_rejected(topic: str) -> None:
    with pytest.raises(ContractError):
        parse_topic(topic)


def test_envelope_round_trips_through_json() -> None:
    envelope = envelope_from_mqtt(TOPIC, encode(PAYLOAD))
    assert envelope.key == f"{TENANT}/CommercialDrone/DRN-0001"
    assert envelope.message.ts == datetime(2026, 11, 14, 17, 0, 0, 123000, tzinfo=UTC)
    assert TelemetryEnvelope.model_validate_json(envelope.model_dump_json()) == envelope


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"ts": "2026-11-14T17:00:00"}, "invalid payload"),  # no time zone
        ({"ts": None}, "invalid payload"),
        ({"metrics": {"BatteryVoltage": 1.0}}, "snake_case"),
        ({"metrics": {"battery_voltage": [1, 2]}}, "invalid payload"),
        ({"position": {"lat": 117.0, "lon": 78.0}}, "invalid payload"),
        ({"extra": 1}, "invalid payload"),
        ({"metrics": {}, "position": None}, "neither metrics nor a position"),
    ],
)
def test_bad_payloads_are_rejected(change: dict, message: str) -> None:
    with pytest.raises(ContractError, match=message):
        envelope_from_mqtt(TOPIC, encode({**PAYLOAD, **change}))


def test_non_json_payload_is_rejected() -> None:
    with pytest.raises(ContractError, match="invalid payload"):
        envelope_from_mqtt(TOPIC, b"\xff\xfe not json")


def test_rows_from_envelope() -> None:
    readings, positions = rows_from_envelope(envelope_from_mqtt(TOPIC, encode(PAYLOAD)))
    ts = datetime(2026, 11, 14, 17, 0, 0, 123000, tzinfo=UTC)
    identity = (ts, uuid.UUID(TENANT), "CommercialDrone", "DRN-0001")
    assert readings == [
        (*identity, "armed", 1.0, None, "mqtt"),  # booleans become 1.0 / 0.0
        (*identity, "battery_voltage", 22.4, None, "mqtt"),
        (*identity, "flight_mode", None, "AUTO", "mqtt"),  # text goes to value_text
    ]
    assert positions == [(*identity, 78.35, 17.44, 120.5, 87.0, 12.1, "mqtt")]  # lon before lat


class FakeProducer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bytes, bytes]] = []
        self.fail_next = False

    def produce(self, topic: str, *, key: bytes, value: bytes) -> None:
        if self.fail_next:
            self.fail_next = False
            raise BufferError("queue full")
        self.sent.append((topic, key, value))

    def poll(self, timeout: float) -> int:
        return 0


def test_bridge_forwards_valid_messages_keyed_by_object() -> None:
    producer = FakeProducer()
    bridge = Bridge(producer, "s2.telemetry.raw", "s2.telemetry.dlq")
    assert bridge.handle(TOPIC, encode(PAYLOAD)) is True
    topic, key, value = producer.sent[0]
    assert (topic, key) == ("s2.telemetry.raw", f"{TENANT}/CommercialDrone/DRN-0001".encode())
    assert parse(value) is not None
    assert (bridge.forwarded, bridge.rejected) == (1, 0)


def test_bridge_sends_invalid_messages_to_the_dlq_with_the_reason() -> None:
    producer = FakeProducer()
    bridge = Bridge(producer, "s2.telemetry.raw", "s2.telemetry.dlq")
    assert bridge.handle(TOPIC, b"{not json") is False
    topic, key, value = producer.sent[0]
    assert (topic, key) == ("s2.telemetry.dlq", TOPIC.encode())
    rejected = json.loads(value)
    assert (
        rejected["topic"] == TOPIC
        and "invalid payload" in rejected["error"]
        and rejected["payload"] == "{not json"
    )
    assert (bridge.forwarded, bridge.rejected) == (0, 1)


def test_bridge_retries_once_when_the_producer_queue_is_full() -> None:
    producer = FakeProducer()
    producer.fail_next = True
    assert Bridge(producer, "raw", "dlq").handle(TOPIC, encode(PAYLOAD)) is True
    assert len(producer.sent) == 1


def test_sink_helpers() -> None:
    assert libpq_url("postgresql+psycopg://u:p@host:5433/telemetry") == "postgresql://u:p@host:5433/telemetry"
    assert libpq_url("postgresql://u@host/db") == "postgresql://u@host/db"
    assert parse(None) is None and parse(b"junk") is None
