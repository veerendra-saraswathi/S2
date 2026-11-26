"""contracts.py against the frozen telemetry contract v1 in contracts/telemetry/v1/.

The schemas and the cases there are the contract; this module is one implementation of it.
Every case must get the same verdict from the JSON Schema and from the code, and the code's
fields must be the schema's. A producer in another repository runs the same cases.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from s2_ingest import contracts
from s2_ingest.contracts import (
    ContractError,
    Position,
    TelemetryEnvelope,
    TelemetryMessage,
    envelope_from_mqtt,
    parse_topic,
)

V1 = Path(__file__).resolve().parents[3] / "contracts" / "telemetry" / "v1"
MESSAGE_SCHEMA = json.loads((V1 / "message.schema.json").read_text())
TOPIC_SCHEMA = json.loads((V1 / "topic.schema.json").read_text())
CASES = json.loads((V1 / "cases.json").read_text())
TOPIC = CASES["topics"][0]["topic"]

message_validator = jsonschema.Draft202012Validator(MESSAGE_SCHEMA)
topic_validator = jsonschema.Draft202012Validator(TOPIC_SCHEMA)


def raw(case: dict[str, Any]) -> bytes:
    if "payload_text" in case:
        return case["payload_text"].encode()
    return json.dumps(case["payload"], ensure_ascii=False).encode()


def code_accepts(topic: str, payload: bytes) -> bool:
    try:
        envelope_from_mqtt(topic, payload)
    except ContractError:
        return False
    return True


def schema_accepts(case: dict[str, Any]) -> bool:
    try:
        payload = json.loads(raw(case))
    except json.JSONDecodeError:
        return False
    return message_validator.is_valid(payload)


@pytest.mark.parametrize("schema", [MESSAGE_SCHEMA, TOPIC_SCHEMA], ids=["message", "topic"])
def test_the_schemas_are_valid_json_schema(schema: dict) -> None:
    jsonschema.Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("case", CASES["messages"], ids=[c["why"] for c in CASES["messages"]])
def test_message_case(case: dict[str, Any]) -> None:
    assert code_accepts(TOPIC, raw(case)) is case["valid"], "contracts.py disagrees with the case"
    if case.get("schema") == "form-only":
        assert schema_accepts(case), "a form-only case must pass the schema, or it is not form-only"
    else:
        assert schema_accepts(case) is case["valid"], "the schema disagrees with the case"


@pytest.mark.parametrize("case", CASES["topics"], ids=[c["why"] for c in CASES["topics"]])
def test_topic_case(case: dict[str, Any]) -> None:
    try:
        parse_topic(case["topic"])
        parsed = True
    except ContractError:
        parsed = False
    assert parsed is case["valid"], "contracts.py disagrees with the case"
    assert topic_validator.is_valid(case["topic"]) is case["valid"], "the schema disagrees with the case"


def test_the_code_has_the_schemas_fields() -> None:
    assert set(TelemetryMessage.model_fields) == set(MESSAGE_SCHEMA["properties"])
    assert set(Position.model_fields) == set(MESSAGE_SCHEMA["$defs"]["position"]["properties"])
    assert MESSAGE_SCHEMA["required"] == ["ts"]
    assert MESSAGE_SCHEMA["$defs"]["position"]["required"] == ["lat", "lon"]


def test_the_code_has_the_schemas_patterns() -> None:
    props = MESSAGE_SCHEMA["properties"]
    assert contracts._TS.pattern == props["ts"]["pattern"]
    assert contracts._METRIC_NAME.pattern == props["metrics"]["propertyNames"]["pattern"]
    assert contracts.TOPIC_FILTER == "s2/v1/+/+/+/telemetry"


def test_what_s2_passes_on_is_still_v1() -> None:
    """The bridge's envelope, read back by the sink, carries a message the schema accepts."""
    for case in (c for c in CASES["messages"] if c["valid"]):
        envelope = envelope_from_mqtt(TOPIC, raw(case))
        again = TelemetryEnvelope.model_validate_json(envelope.model_dump_json())
        assert again == envelope, case["why"]
        message_validator.validate(json.loads(again.message.model_dump_json()))


def test_the_cases_say_what_they_claim() -> None:
    names = {
        name
        for c in CASES["messages"]
        if isinstance(c.get("payload"), dict)
        for name in c["payload"].get("metrics") or {}
    }
    assert {len(n) for n in names if n.startswith("a2345")} == {63, 64}
    keys = [json.dumps(c.get("payload", c.get("payload_text")), sort_keys=True) for c in CASES["messages"]]
    assert len(keys) == len(set(keys)), "two cases test the same payload"
    assert all(isinstance(c["valid"], bool) and c["why"] for c in CASES["messages"] + CASES["topics"])
    assert {c.get("schema") for c in CASES["messages"]} <= {None, "form-only"}
    assert re.fullmatch(TOPIC_SCHEMA["pattern"], TOPIC)
