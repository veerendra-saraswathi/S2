"""Authentication, the ontology registry, objects, links and tenant isolation."""

from __future__ import annotations

import copy
import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from s2_common.auth import TokenVerifier
from s2_common.opa import OpaClient
from s2_common.settings import Settings
from s2_ontology.app import create_app
from s2_ontology.domain import objects

from .conftest import AUDIENCE, ISSUER, Auth, MakeToken, load_pack

POINT = {"type": "Point", "coordinates": [78.3489, 17.4401]}


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #


def test_health_needs_no_token(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    ready = client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["checks"] == {"database": True, "policy_engine": True}


def test_requests_without_a_valid_token_are_rejected(
    client: TestClient, make_token: MakeToken, tenant: uuid.UUID
) -> None:
    def status(token: str | None) -> int:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return client.get("/v1/me", headers=headers).status_code

    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    base = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "mallory",
        "iat": now,
        "exp": now + 60,
        "tenant_id": str(tenant),
    }

    assert status(None) == 401
    assert status("not-a-jwt") == 401
    assert status(make_token("operator", tenant=tenant, key=other_key)) == 401  # signed by someone else
    assert status(make_token("operator", tenant=tenant, aud="another-api")) == 401
    assert status(make_token("operator", tenant=tenant, iss="https://evil.test/realms/s2")) == 401
    assert status(make_token("operator", tenant=tenant, exp=now - 3600)) == 401
    assert status(make_token("operator", tenant=None)) == 401  # no tenant claim
    assert status(make_token("operator", tenant="not-a-uuid")) == 401
    assert status(jwt.encode(base, key=None, algorithm="none")) == 401  # unsigned
    assert status(jwt.encode(base, "shared-secret-shared-secret-shared-secret", algorithm="HS256")) == 401

    assert status(make_token("operator", tenant=tenant)) == 200


def test_me_reflects_the_token(client: TestClient, auth: Auth, tenant: uuid.UUID) -> None:
    body = client.get("/v1/me", headers=auth("operator", "approver")).json()
    assert body["tenant_id"] == str(tenant)
    assert body["roles"] == ["approver", "operator"]
    assert body["actor_type"] == "user"


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


def test_applying_a_pack_is_idempotent(client: TestClient, auth: Auth) -> None:
    admin = auth("ontology-admin")
    first = client.post("/v1/ontology/packs:apply", json=load_pack("core-fleet"), headers=admin).json()
    assert "ObjectType:CommercialDrone" in first["created"]
    assert "ActionType:groundDrone" in first["created"]
    assert first["updated"] == [] and first["unchanged"] == []

    second = client.post("/v1/ontology/packs:apply", json=load_pack("core-fleet"), headers=admin).json()
    assert second["created"] == [] and second["updated"] == []
    assert sorted(second["unchanged"]) == sorted(first["created"])

    drone = client.get("/v1/ontology/object-types/CommercialDrone", headers=auth("viewer")).json()
    assert drone["version"] == 1 and drone["status"] == "active"
    assert {p["api_name"] for p in drone["properties"]} >= {"droneId", "status", "batteryVoltage"}


def test_only_an_ontology_admin_may_apply_a_pack(client: TestClient, auth: Auth) -> None:
    response = client.post("/v1/ontology/packs:apply", json=load_pack("core-fleet"), headers=auth("operator"))
    assert response.status_code == 403
    assert client.get("/v1/ontology/object-types", headers=auth("operator")).json() == []


def test_a_pack_that_needs_another_pack_says_so(client: TestClient, auth: Auth) -> None:
    response = client.post(
        "/v1/ontology/packs:apply", json=load_pack("energy-grid"), headers=auth("ontology-admin")
    )
    assert response.status_code == 422
    assert "unknown object type 'CommercialDrone'" in response.json()["detail"]["message"]
    # Nothing from the failed pack was kept.
    assert client.get("/v1/ontology/object-types", headers=auth("viewer")).json() == []


def test_evolving_a_pack(client: TestClient, auth: Auth, ontology: None) -> None:
    admin = auth("ontology-admin")
    pack = copy.deepcopy(load_pack("core-fleet"))
    drone = pack["object_types"][0]
    drone["properties"].append(
        {"api_name": "serialNumber", "display_name": "Serial number", "data_type": "string"}
    )
    drone["properties"] = [p for p in drone["properties"] if p["api_name"] != "model"]

    result = client.post("/v1/ontology/packs:apply", json=pack, headers=admin).json()
    assert result["updated"] == ["ObjectType:CommercialDrone"]

    after = client.get("/v1/ontology/object-types/CommercialDrone", headers=admin).json()
    statuses = {p["api_name"]: p["status"] for p in after["properties"]}
    assert after["version"] == 2
    assert statuses["serialNumber"] == "active"
    assert statuses["model"] == "deprecated"  # removed from the pack: kept, but no longer writable

    write = client.put(
        "/v1/objects/CommercialDrone/DRN-9",
        json={"properties": {"callSign": "x", "status": "READY", "model": "X8"}},
        headers=admin,
    )
    assert write.status_code == 422 and "deprecated" in write.json()["detail"]["message"]


def test_breaking_changes_to_a_property_are_refused(client: TestClient, auth: Auth, ontology: None) -> None:
    pack = copy.deepcopy(load_pack("core-fleet"))
    model = next(p for p in pack["object_types"][0]["properties"] if p["api_name"] == "model")
    model["data_type"] = "integer"
    response = client.post("/v1/ontology/packs:apply", json=pack, headers=auth("ontology-admin"))
    assert response.status_code == 422
    assert "changing data_type would reinterpret existing data" in response.json()["detail"]["message"]


def test_the_title_can_move_between_properties(client: TestClient, auth: Auth, ontology: None) -> None:
    pack = copy.deepcopy(load_pack("core-fleet"))
    properties = {p["api_name"]: p for p in pack["object_types"][0]["properties"]}
    properties["callSign"]["is_title"] = False
    properties["model"]["is_title"] = True
    response = client.post("/v1/ontology/packs:apply", json=pack, headers=auth("ontology-admin"))
    assert response.status_code == 200, response.text
    after = client.get("/v1/ontology/object-types/CommercialDrone", headers=auth("viewer")).json()
    assert [p["api_name"] for p in after["properties"] if p["is_title"]] == ["model"]


def test_an_action_that_could_never_run_is_refused(client: TestClient, auth: Auth, ontology: None) -> None:
    admin = auth("ontology-admin")

    def apply(mutate) -> str:
        pack = copy.deepcopy(load_pack("core-fleet"))
        mutate(pack["action_types"][0])
        response = client.post("/v1/ontology/packs:apply", json=pack, headers=admin)
        assert response.status_code == 422, response.text
        return response.json()["detail"]["message"]

    assert "no writable property ['altitude']" in apply(lambda a: a["edits"][0]["assign"].update(altitude=10))
    assert "undeclared parameters ['pilot']" in apply(
        lambda a: a["edits"][0]["assign"].update(groundedReason="{{ params.pilot }}")
    )
    assert "unknown object type 'Helicopter'" in apply(
        lambda a: a["edits"][0].update(object_type="Helicopter")
    )
    assert "not a valid JSON Schema" in apply(
        lambda a: a["parameters"]["properties"].update(droneId={"type": 7})
    )


def test_links_and_actions_are_listed(client: TestClient, auth: Auth, ontology: None) -> None:
    viewer = auth("viewer")
    links = {link["api_name"]: link for link in client.get("/v1/ontology/link-types", headers=viewer).json()}
    assert links["inspects"]["source_object_type"] == "CommercialDrone"
    assert links["inspects"]["target_object_type"] == "Substation"
    actions = {a["api_name"]: a for a in client.get("/v1/ontology/action-types", headers=viewer).json()}
    assert actions["groundDrone"]["policy_path"] == "s2/actions/ground_drone"
    assert actions["returnDroneToService"]["requires_approval"] is True
    assert actions["createWorkOrder"]["policy_path"] == "s2/actions/standard"


# --------------------------------------------------------------------------- #
# Objects and links
# --------------------------------------------------------------------------- #


def test_object_round_trip(client: TestClient, auth: Auth, ontology: None) -> None:
    admin, viewer = auth("ontology-admin"), auth("viewer")
    body = {"properties": {"name": "Gachibowli 220 kV", "voltageKv": 220, "location": POINT}}
    created = client.put("/v1/objects/Substation/SS-HYD-07", json=body, headers=admin)
    assert created.status_code == 200, created.text
    assert created.json()["version"] == 1

    fetched = client.get("/v1/objects/Substation/SS-HYD-07", headers=viewer).json()
    assert fetched["properties"] == {
        "substationId": "SS-HYD-07",  # the primary key comes back under its property name
        "name": "Gachibowli 220 kV",
        "voltageKv": 220,
        "location": POINT,
    }

    # PUT replaces: properties left out are gone, and the version advances.
    replaced = client.put(
        "/v1/objects/Substation/SS-HYD-07", json={"properties": {"name": "Renamed"}}, headers=admin
    )
    assert replaced.json()["properties"] == {"substationId": "SS-HYD-07", "name": "Renamed"}
    assert replaced.json()["version"] == 2

    listed = client.get("/v1/objects/Substation", headers=viewer).json()
    assert [o["primary_key"] for o in listed] == ["SS-HYD-07"]

    assert client.delete("/v1/objects/Substation/SS-HYD-07", headers=admin).status_code == 204
    assert client.get("/v1/objects/Substation/SS-HYD-07", headers=viewer).status_code == 404


def test_object_writes_are_validated_and_restricted(client: TestClient, auth: Auth, ontology: None) -> None:
    admin = auth("ontology-admin")
    url = "/v1/objects/CommercialDrone/DRN-1"
    valid = {"properties": {"callSign": "Garuda-1", "status": "READY"}}

    assert client.put(url, json=valid, headers=auth("operator")).status_code == 403
    assert client.put(url, json=valid, headers=auth("viewer")).status_code == 403

    def error(properties: dict) -> str:
        response = client.put(url, json={"properties": properties}, headers=admin)
        assert response.status_code == 422, response.text
        return response.json()["detail"]["message"]

    assert "must be one of" in error({"callSign": "x", "status": "FLYING"})
    assert "unknown property" in error({"callSign": "x", "status": "READY", "wings": 4})
    assert "required property is missing" in error({"status": "READY"})
    assert "written by ingestion" in error({"callSign": "x", "status": "READY", "batteryVoltage": 22.1})
    assert client.put("/v1/objects/Helicopter/H-1", json=valid, headers=admin).status_code == 404
    assert client.get(url, headers=admin).status_code == 404  # none of the rejected writes left anything


def test_links_are_followed_in_both_directions(client: TestClient, auth: Auth, drone: str) -> None:
    admin, viewer = auth("ontology-admin"), auth("viewer")
    for key in ("SS-1", "SS-2"):
        client.put(f"/v1/objects/Substation/{key}", json={"properties": {"name": key}}, headers=admin)
    for key in ("SS-1", "SS-2"):
        linked = client.post(
            f"/v1/objects/CommercialDrone/{drone}/links/inspects",
            json={"target_primary_key": key},
            headers=admin,
        )
        assert linked.status_code == 204, linked.text

    forward = client.get(f"/v1/objects/CommercialDrone/{drone}/links/inspects", headers=viewer).json()
    assert [o["primary_key"] for o in forward] == ["SS-1", "SS-2"]
    assert forward[0]["object_type"] == "Substation"

    reverse = client.get("/v1/objects/Substation/SS-1/links/inspectedBy", headers=viewer).json()
    assert [o["primary_key"] for o in reverse] == [drone]

    assert (
        client.get(f"/v1/objects/CommercialDrone/{drone}/links/nonsense", headers=viewer).status_code == 404
    )
    missing = client.post(
        f"/v1/objects/CommercialDrone/{drone}/links/inspects",
        json={"target_primary_key": "SS-404"},
        headers=admin,
    )
    assert missing.status_code == 404


def test_link_cardinality_is_enforced(client: TestClient, auth: Auth, drone: str) -> None:
    admin = auth("ontology-admin")
    for key in ("M-1", "M-2"):
        mission = {"properties": {"name": key, "status": "PLANNED"}}
        assert (
            client.put(f"/v1/objects/InspectionMission/{key}", json=mission, headers=admin).status_code == 200
        )

    def assign(mission: str) -> int:
        url = f"/v1/objects/CommercialDrone/{drone}/links/assignedTo"
        return client.post(url, json={"target_primary_key": mission}, headers=admin).status_code

    assert assign("M-1") == 204
    assert assign("M-1") == 204  # linking again is a no-op
    assert assign("M-2") == 409  # assignedTo is many-to-one: a drone has one mission


def test_timeseries_without_a_telemetry_store(client: TestClient, auth: Auth, drone: str) -> None:
    viewer = auth("viewer")
    base = f"/v1/objects/CommercialDrone/{drone}/timeseries"
    assert client.get(f"{base}/batteryVoltage", headers=viewer).status_code == 503
    assert client.get(f"{base}/callSign", headers=viewer).status_code == 404


def test_a_lost_race_is_a_conflict_not_a_server_error(
    client: TestClient, auth: Auth, ontology: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two writers racing for one key end in a unique violation for the loser."""

    def lose_the_race(*_args: object, **_kwargs: object) -> None:
        raise IntegrityError("INSERT", {}, Exception("duplicate key value violates unique constraint"))

    monkeypatch.setattr(objects, "write", lose_the_race)
    body = {"properties": {"name": "SS-1"}}
    response = client.put("/v1/objects/Substation/SS-1", json=body, headers=auth("ontology-admin"))
    assert response.status_code == 409
    assert "concurrent change" in response.json()["detail"]["message"]
    monkeypatch.undo()
    assert client.get("/v1/objects/Substation/SS-1", headers=auth("viewer")).status_code == 404


# --------------------------------------------------------------------------- #
# Tenant isolation
# --------------------------------------------------------------------------- #


def test_tenants_cannot_see_each_other(client: TestClient, auth: Auth, drone: str) -> None:
    other = uuid.uuid4()
    theirs = auth("ontology-admin", "viewer", "auditor", tenant_id=other)

    assert client.get("/v1/ontology/object-types", headers=theirs).json() == []
    assert client.get(f"/v1/objects/CommercialDrone/{drone}", headers=theirs).status_code == 404
    assert client.get("/v1/audit", headers=theirs).json() == []

    # The other tenant can define the very same names without colliding with ours.
    applied = client.post("/v1/ontology/packs:apply", json=load_pack("core-fleet"), headers=theirs)
    assert applied.status_code == 200
    assert "ObjectType:CommercialDrone" in applied.json()["created"]
    assert client.get("/v1/objects/CommercialDrone", headers=theirs).json() == []

    mine = client.get(f"/v1/objects/CommercialDrone/{drone}", headers=auth("viewer"))
    assert mine.status_code == 200


def test_the_app_refuses_to_start_as_a_role_that_bypasses_tenant_isolation(
    database: dict[str, str], opa_url: str, verifier: TokenVerifier
) -> None:
    """Row-level security does not apply to superusers. Pointing the service at the
    owner's connection string must be a startup failure, not a silent data leak."""
    settings = Settings(
        database_url=database["owner"],
        telemetry_database_url=None,
        opa_url=opa_url,
        oidc_issuer=ISSUER,
        oidc_audience=AUDIENCE,
    )
    app = create_app(settings, verifier=verifier, opa=OpaClient(opa_url))
    with pytest.raises(RuntimeError, match="bypasses row-level security"), TestClient(app):
        pass
