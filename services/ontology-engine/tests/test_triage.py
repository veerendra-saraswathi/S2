"""Triage rules: registration checks, evaluation over properties and live telemetry, markings."""

from __future__ import annotations

import copy
import datetime as dt
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from s2_common.auth import TokenVerifier
from s2_common.opa import OpaClient
from s2_ontology.app import create_app

from .conftest import Auth, _settings, load_pack


@pytest.fixture(scope="module")
def telemetry(owner_engine: Engine) -> Engine:
    """A telemetry table in the test database (TimescaleDB is not needed to read it)."""
    with owner_engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS telemetry"))
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS telemetry.reading (time timestamptz NOT NULL, "
                "tenant_id uuid NOT NULL, "
                "object_type text NOT NULL, object_pk text NOT NULL, metric text NOT NULL, "
                "value double precision, "
                "value_text text, source text NOT NULL DEFAULT 'test', "
                "PRIMARY KEY (tenant_id, object_type, object_pk, metric, time))"
            )
        )
    return owner_engine


@pytest.fixture(scope="module")
def tclient(
    database: dict[str, str], opa_url: str, verifier: TokenVerifier, telemetry: Engine
) -> Iterator[TestClient]:
    settings = _settings(database, opa_url).model_copy(update={"telemetry_database_url": database["owner"]})
    app = create_app(settings, verifier=verifier, opa=OpaClient(opa_url))
    with TestClient(app) as client:
        yield client


def reading(
    telemetry: Engine, tenant: uuid.UUID, key: str, metric: str, value: float, age_s: float = 5
) -> None:
    with telemetry.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO telemetry.reading (time, tenant_id, object_type, object_pk, metric, value) "
                "VALUES (:t, :tenant, 'CommercialDrone', :key, :metric, :value)"
            ),
            {
                "t": dt.datetime.now(dt.UTC) - dt.timedelta(seconds=age_s),
                "tenant": tenant,
                "key": key,
                "metric": metric,
                "value": value,
            },
        )


@pytest.fixture
def fleet(tclient: TestClient, auth: Auth) -> None:
    admin = auth("ontology-admin", "marking:internal")
    for name in ("core-fleet", "energy-grid"):
        assert (
            tclient.post("/v1/ontology/packs:apply", json=load_pack(name), headers=admin).status_code == 200
        )
    for key, status in (("D1", "READY"), ("D2", "READY"), ("D3", "GROUNDED")):
        body = {"properties": {"callSign": key, "status": status}}
        assert tclient.put(f"/v1/objects/CommercialDrone/{key}", json=body, headers=admin).status_code == 200


def queue(client: TestClient, headers: dict[str, str], name: str) -> dict[str, Any]:
    response = client.get(f"/v1/triage/queues/{name}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def keys(q: dict[str, Any]) -> list[str]:
    return [item["object"]["primary_key"] for item in q["items"]]


def test_a_property_rule(tclient: TestClient, auth: Auth, fleet: None) -> None:
    q = queue(tclient, auth("viewer"), "groundedDrones")
    assert keys(q) == ["D3"]
    assert q["rule"]["actions"] == ["returnDroneToService"]
    assert q["items"][0]["evidence"] == [
        {"property": "status", "op": "eq", "expected": "GROUNDED", "actual": "GROUNDED", "observed_at": None}
    ]


def test_a_telemetry_rule_uses_the_latest_fresh_reading(
    tclient: TestClient, auth: Auth, telemetry: Engine, tenant: uuid.UUID, fleet: None
) -> None:
    operator = auth("operator", "marking:internal")
    reading(telemetry, tenant, "D1", "actuator_health", 0.95, age_s=60)
    reading(telemetry, tenant, "D1", "actuator_health", 0.72, age_s=5)  # D1 is degrading now
    reading(telemetry, tenant, "D2", "actuator_health", 0.60, age_s=60)
    reading(telemetry, tenant, "D2", "actuator_health", 0.91, age_s=5)  # D2 recovered
    reading(telemetry, tenant, "D3", "actuator_health", 0.50, age_s=5)  # bad, but already grounded

    q = queue(tclient, operator, "degradingActuators")
    assert keys(q) == ["D1"]
    first = q["items"][0]["evidence"][0]
    assert (first["property"], first["op"], first["expected"], first["actual"]) == (
        "actuatorHealth",
        "lt",
        0.8,
        0.72,
    )
    assert first["observed_at"] is not None

    # Grounding takes it out of the queue: the ne-GROUNDED condition stops holding.
    grounded = tclient.post(
        "/v1/actions/groundDrone/apply",
        json={"parameters": {"droneId": "D1", "reason": "triage"}},
        headers=operator,
    )
    assert grounded.status_code == 200, grounded.text
    assert keys(queue(tclient, operator, "degradingActuators")) == []
    assert sorted(keys(queue(tclient, operator, "groundedDrones"))) == ["D1", "D3"]


def test_a_stale_reading_does_not_count(
    tclient: TestClient, auth: Auth, telemetry: Engine, tenant: uuid.UUID, fleet: None
) -> None:
    reading(telemetry, tenant, "D2", "battery_voltage", 21.1, age_s=3600)  # older than max_age_s (600)
    assert keys(queue(tclient, auth("viewer"), "lowBattery")) == []
    reading(telemetry, tenant, "D2", "battery_voltage", 21.4, age_s=10)
    assert keys(queue(tclient, auth("viewer"), "lowBattery")) == ["D2"]


def test_the_list_counts_each_queue(
    tclient: TestClient, auth: Auth, telemetry: Engine, tenant: uuid.UUID, fleet: None
) -> None:
    reading(telemetry, tenant, "D2", "actuator_health", 0.70)
    counts = {
        q["rule"]["api_name"]: q["count"]
        for q in tclient.get("/v1/triage/queues", headers=auth("operator", "marking:internal")).json()
    }
    assert counts == {"degradingActuators": 1, "groundedDrones": 1, "lowBattery": 0}
    assert all(
        "items" not in q or q["items"] is None
        for q in tclient.get("/v1/triage/queues", headers=auth("viewer")).json()
    )


def test_a_rule_over_a_marked_series_is_hidden_from_the_uncleared(
    tclient: TestClient, auth: Auth, fleet: None
) -> None:
    names = [q["rule"]["api_name"] for q in tclient.get("/v1/triage/queues", headers=auth("viewer")).json()]
    assert "degradingActuators" not in names, "actuatorHealth is marked internal"
    assert "lowBattery" in names
    assert tclient.get("/v1/triage/queues/degradingActuators", headers=auth("viewer")).status_code == 404
    assert tclient.get("/v1/triage/queues/nothing", headers=auth("viewer")).status_code == 404


def test_without_telemetry_a_series_rule_warns_and_matches_nothing(
    client: TestClient, auth: Auth, ontology: None
) -> None:
    q = client.get("/v1/triage/queues/lowBattery", headers=auth("viewer")).json()
    assert q["count"] == 0 and "telemetry" in q["warning"]


def test_queues_are_per_tenant(tclient: TestClient, auth: Auth, fleet: None) -> None:
    assert tclient.get("/v1/triage/queues", headers=auth("viewer", tenant_id=uuid.uuid4())).json() == []


@pytest.mark.parametrize(
    ("rule", "message"),
    [
        ({"when": [{"property": "nope", "op": "eq", "value": 1}]}, "no active property 'nope'"),
        ({"when": [{"property": "callSign", "op": "lt", "value": "x"}]}, "needs a number"),
        ({"when": [{"property": "batteryVoltage", "op": "eq", "value": 1}]}, "lt, le, gt or ge"),
        ({"when": [{"property": "batteryVoltage", "op": "lt", "value": "low"}]}, "compare it with a number"),
        ({"when": [{"property": "status", "op": "eq", "value": "BROKEN"}]}, "takes only"),
        ({"when": [{"property": "status", "op": "in", "value": []}]}, "non-empty list"),
        (
            {"when": [{"property": "status", "op": "eq", "value": "READY", "max_age_s": 60}]},
            "time series only",
        ),
        ({"when": [{"property": "position", "op": "eq", "value": 1}]}, "cannot be tested"),
        ({"actions": ["createWorkOrder"]}, "does not act on CommercialDrone"),
        ({"actions": ["fly"]}, "unknown action 'fly'"),
    ],
)
def test_a_rule_that_could_never_work_is_refused(
    client: TestClient, auth: Auth, ontology: None, rule: dict[str, Any], message: str
) -> None:
    pack = copy.deepcopy(load_pack("core-fleet"))
    base = {
        "api_name": "bad",
        "display_name": "Bad",
        "object_type": "CommercialDrone",
        "when": [{"property": "status", "op": "eq", "value": "READY"}],
    }
    pack["triage_rules"] = [{**base, **rule}]
    response = client.post("/v1/ontology/packs:apply", json=pack, headers=auth("ontology-admin"))
    assert response.status_code == 422, response.text
    assert message in response.text


def test_rules_are_applied_idempotently(client: TestClient, auth: Auth, ontology: None) -> None:
    again = client.post(
        "/v1/ontology/packs:apply", json=load_pack("core-fleet"), headers=auth("ontology-admin")
    ).json()
    assert "TriageRule:degradingActuators" in again["unchanged"]
