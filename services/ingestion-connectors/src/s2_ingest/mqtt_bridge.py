"""MQTT -> Kafka bridge.

Subscribes to the fleet telemetry topic tree, validates every message against the
contract, and forwards it to Kafka keyed by object so each object's readings stay in
order. Kafka is the durable, replayable log: every downstream store is fed from it.

Delivery: Kafka production is idempotent and ordered per key. The MQTT hop is
acknowledged on receipt, so a bridge crash can drop messages that were in flight.
That is acceptable for periodic telemetry; if a feed ever needs stronger guarantees,
switch this client to manual acknowledgements tied to the Kafka delivery report.

Run:  python -m s2_ingest.mqtt_bridge
"""

from __future__ import annotations

import json
import logging
import signal
from typing import Any, Protocol
from urllib.parse import urlparse

import paho.mqtt.client as mqtt
from confluent_kafka import Producer

from s2_common.logging import configure_logging
from s2_common.settings import get_settings
from s2_ingest.contracts import TOPIC_FILTER, ContractError, envelope_from_mqtt

log = logging.getLogger("s2.ingest.mqtt_bridge")


class KafkaProducer(Protocol):
    def produce(self, topic: str, *, key: bytes, value: bytes) -> None: ...
    def poll(self, timeout: float) -> int: ...


class Bridge:
    """The forwarding logic, independent of the MQTT and Kafka clients' lifecycles."""

    def __init__(self, producer: KafkaProducer, topic: str, dlq_topic: str) -> None:
        self._producer = producer
        self._topic = topic
        self._dlq_topic = dlq_topic
        self.forwarded = 0
        self.rejected = 0

    def handle(self, topic: str, payload: bytes) -> bool:
        """Forward one MQTT message. Returns False if it was rejected to the DLQ."""
        try:
            envelope = envelope_from_mqtt(topic, payload)
        except ContractError as exc:
            self.rejected += 1
            log.warning("rejected message on %s: %s", topic, exc)
            reason = {"topic": topic, "error": str(exc), "payload": payload.decode("utf-8", "replace")}
            self._produce(self._dlq_topic, topic.encode(), json.dumps(reason).encode())
            return False
        self._produce(self._topic, envelope.key.encode(), envelope.model_dump_json().encode())
        self.forwarded += 1
        return True

    def _produce(self, topic: str, key: bytes, value: bytes) -> None:
        try:
            self._producer.produce(topic, key=key, value=value)
        except BufferError:
            # Local queue is full: let deliveries drain, then try once more.
            self._producer.poll(1.0)
            self._producer.produce(topic, key=key, value=value)
        self._producer.poll(0)


def read_secret(path: str) -> str:
    """A password from a file, as Compose mounts a secret: one line, newline stripped."""
    with open(path) as f:
        return f.read().strip()


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    producer = Producer(
        {
            "bootstrap.servers": settings.kafka_bootstrap,
            "client.id": "s2-mqtt-bridge",
            "enable.idempotence": True,
            "linger.ms": 20,
        }
    )
    bridge = Bridge(producer, settings.telemetry_topic, settings.telemetry_dlq_topic)

    broker = urlparse(settings.mqtt_url)
    # A fixed client id with a persistent session: the broker queues QoS 1 messages
    # while the bridge restarts. Run a single replica (or move to MQTT 5 shared
    # subscriptions) so two bridges never fight over the same session.
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="s2-mqtt-bridge", clean_session=False)
    username = settings.mqtt_username or broker.username
    password = read_secret(settings.mqtt_password_file) if settings.mqtt_password_file else broker.password
    if username:
        client.username_pw_set(username, password)
    tls = broker.scheme in ("mqtts", "ssl")
    if tls:
        client.tls_set(ca_certs=settings.mqtt_ca_file)
    port = broker.port or (8883 if tls else 1883)

    def on_connect(_client: mqtt.Client, _userdata: Any, _flags: Any, reason_code: Any, _props: Any) -> None:
        if reason_code.is_failure:
            log.error("MQTT connection refused: %s", reason_code)
            return
        log.info("connected to %s:%s as %s, subscribing to %s", broker.hostname, port, username, TOPIC_FILTER)
        client.subscribe(TOPIC_FILTER, qos=1)

    def on_message(_client: mqtt.Client, _userdata: Any, message: mqtt.MQTTMessage) -> None:
        bridge.handle(message.topic, message.payload)

    client.on_connect = on_connect
    client.on_message = on_message

    def stop(*_: Any) -> None:
        log.info("stopping: forwarded=%d rejected=%d", bridge.forwarded, bridge.rejected)
        client.disconnect()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    client.connect(broker.hostname or "localhost", port, keepalive=30)
    client.loop_forever(retry_first_connection=True)
    producer.flush(10)


if __name__ == "__main__":
    run()
