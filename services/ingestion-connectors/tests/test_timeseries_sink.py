"""The sink's batching: how long it waits, and what it skips."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from confluent_kafka import KafkaError, KafkaException

from s2_common.settings import Settings
from s2_ingest.contracts import envelope_from_mqtt
from s2_ingest.timeseries_sink import commit, next_batch

TOPIC = "s2/v1/00000000-0000-0000-0000-000000000001/CommercialDrone/DRN-0001/telemetry"


class FakeError:
    def __init__(self, code: int) -> None:
        self._code = code

    def code(self) -> int:
        return self._code


class FakeMessage:
    def __init__(self, value: bytes | None = None, error: FakeError | None = None) -> None:
        self._value, self._error = value, error

    def value(self) -> bytes | None:
        return self._value

    def error(self) -> FakeError | None:
        return self._error


class FakeConsumer:
    def __init__(self, messages: list[FakeMessage]) -> None:
        self.messages = messages
        self.calls: list[dict[str, Any]] = []

    def consume(self, *, num_messages: int, timeout: float) -> list[FakeMessage]:
        self.calls.append({"num_messages": num_messages, "timeout": timeout})
        return self.messages[:num_messages]


def envelope_bytes() -> bytes:
    ts = datetime.now(UTC).isoformat()
    payload = f'{{"ts": "{ts}", "metrics": {{"battery_voltage": 24.1}}}}'.encode()
    return envelope_from_mqtt(TOPIC, payload).model_dump_json().encode()


def test_the_default_wait_keeps_light_traffic_fast() -> None:
    # A 1 s wait added up to a second to every reading when traffic was light: the
    # sink waited for 500 messages that were not coming.
    assert Settings().sink_batch_wait_s <= 0.2
    assert Settings().sink_batch_size >= 100


def test_a_batch_uses_the_configured_size_and_wait() -> None:
    consumer = FakeConsumer([FakeMessage(envelope_bytes()) for _ in range(3)])
    envelopes, consumed = next_batch(consumer, 250, 0.05)
    assert consumer.calls == [{"num_messages": 250, "timeout": 0.05}]
    assert (len(envelopes), consumed) == (3, 3)


def test_errors_and_malformed_envelopes_are_skipped_but_counted_as_consumed() -> None:
    consumer = FakeConsumer(
        [
            FakeMessage(envelope_bytes()),
            FakeMessage(b"not json"),
            FakeMessage(error=FakeError(KafkaError.UNKNOWN_TOPIC_OR_PART)),
            FakeMessage(None),
        ]
    )
    envelopes, consumed = next_batch(consumer, 500, 0.1)
    # The malformed one and the tombstone are consumed, so their offsets are committed
    # and they do not block the partition; the error frame is not a message.
    assert (len(envelopes), consumed) == (1, 3)


class CommittingConsumer:
    def __init__(self, error: KafkaException | None = None) -> None:
        self.error, self.commits = error, 0

    def commit(self, asynchronous: bool = True) -> None:
        assert asynchronous is False
        self.commits += 1
        if self.error is not None:
            raise self.error


def test_a_commit_is_synchronous() -> None:
    consumer = CommittingConsumer()
    assert commit(consumer) is True
    assert consumer.commits == 1


def test_a_commit_refused_after_a_rebalance_does_not_stop_the_sink() -> None:
    # Two sinks share the partitions; one lost its partitions between write and commit.
    refused = KafkaException(KafkaError(KafkaError.ILLEGAL_GENERATION))
    assert commit(CommittingConsumer(refused)) is False
