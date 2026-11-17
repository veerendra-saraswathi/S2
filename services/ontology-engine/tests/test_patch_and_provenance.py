"""PATCH (merge writes) and write provenance: the two things a pipeline sync needs."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from .conftest import Auth

DRONE = {"callSign": "Garuda-1", "model": "X8", "status": "READY"}
SOURCE = {"dataset": "assets.commercial_drone", "snapshot_id": "7461205316476390859", "branch": "main"}


def make_drone(client: TestClient, auth: Auth) -> dict[str, str]:
    admin = auth("ontology-admin")
    assert (
        client.put("/v1/objects/CommercialDrone/D1", json={"properties": DRONE}, headers=admin).status_code
        == 200
    )
    return admin


def audit(client: TestClient, auth: Auth, **filters: str) -> list[dict[str, Any]]:
    return client.get("/v1/audit", params=filters, headers=auth("auditor")).json()


def test_patch_changes_only_what_it_names(client: TestClient, auth: Auth, ontology: None) -> None:
    admin = make_drone(client, auth)
    patched = client.patch(
        "/v1/objects/CommercialDrone/D1",
        json={"properties": {"model": "X9", "firmwareVersion": "4.2.0"}},
        headers=admin,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["properties"] == {
        "droneId": "D1",
        **DRONE,
        "model": "X9",
        "firmwareVersion": "4.2.0",
    }
    assert patched.json()["version"] == 2

    (entry,) = audit(client, auth, event_type="object.modified")
    assert entry["details"]["before"] == {"model": "X8", "firmwareVersion": None}
    assert entry["details"]["after"] == {"model": "X9", "firmwareVersion": "4.2.0"}


def test_patch_null_clears_but_not_a_required_property(
    client: TestClient, auth: Auth, ontology: None
) -> None:
    admin = make_drone(client, auth)
    cleared = client.patch(
        "/v1/objects/CommercialDrone/D1", json={"properties": {"model": None}}, headers=admin
    )
    assert "model" not in cleared.json()["properties"]
    refused = client.patch(
        "/v1/objects/CommercialDrone/D1", json={"properties": {"status": None}}, headers=admin
    )
    assert refused.status_code == 422


def test_patch_needs_the_object_and_the_role(client: TestClient, auth: Auth, ontology: None) -> None:
    admin = auth("ontology-admin")
    missing = client.patch(
        "/v1/objects/CommercialDrone/NOPE", json={"properties": {"model": "X"}}, headers=admin
    )
    assert missing.status_code == 404
    make_drone(client, auth)
    operator = auth("operator")
    denied = client.patch(
        "/v1/objects/CommercialDrone/D1", json={"properties": {"model": "X"}}, headers=operator
    )
    assert denied.status_code == 403


def test_a_patch_that_changes_nothing_is_not_audited(client: TestClient, auth: Auth, ontology: None) -> None:
    admin = make_drone(client, auth)
    same = client.patch("/v1/objects/CommercialDrone/D1", json={"properties": {"model": "X8"}}, headers=admin)
    assert same.status_code == 200
    assert same.json()["version"] == 1
    assert audit(client, auth, event_type="object.modified") == []


def test_patch_respects_markings(client: TestClient, auth: Auth, ontology: None) -> None:
    pack = {
        "name": "patch-markings",
        "version": "0.1.0",
        "object_types": [
            {
                "api_name": "Asset",
                "display_name": "Asset",
                "properties": [
                    {
                        "api_name": "assetId",
                        "display_name": "ID",
                        "data_type": "string",
                        "is_primary_key": True,
                    },
                    {
                        "api_name": "cost",
                        "display_name": "Cost",
                        "data_type": "integer",
                        "markings": ["finance"],
                    },
                ],
            }
        ],
    }
    full = auth("ontology-admin", "marking:finance")
    assert client.post("/v1/ontology/packs:apply", json=pack, headers=full).status_code == 200
    assert (
        client.put("/v1/objects/Asset/A1", json={"properties": {"cost": 5}}, headers=full).status_code == 200
    )
    refused = client.patch(
        "/v1/objects/Asset/A1", json={"properties": {"cost": 9}}, headers=auth("ontology-admin")
    )
    assert refused.status_code == 403
    assert refused.json()["detail"]["properties"] == ["cost"]


def test_provenance_is_recorded_with_the_change(client: TestClient, auth: Auth, ontology: None) -> None:
    admin = auth("ontology-admin")
    created = client.put(
        "/v1/objects/CommercialDrone/D1", json={"properties": DRONE, "provenance": SOURCE}, headers=admin
    )
    assert created.status_code == 200, created.text
    client.patch(
        "/v1/objects/CommercialDrone/D1",
        json={"properties": {"model": "X9"}, "provenance": {**SOURCE, "branch": "preview", "run_id": "r-1"}},
        headers=admin,
    )
    (upserted,) = audit(client, auth, event_type="object.upserted")
    assert upserted["details"]["provenance"] == {**SOURCE, "run_id": None}
    (modified,) = audit(client, auth, event_type="object.modified")
    assert modified["details"]["provenance"]["branch"] == "preview"
    assert modified["details"]["provenance"]["run_id"] == "r-1"


def test_provenance_is_validated(client: TestClient, auth: Auth, ontology: None) -> None:
    admin = auth("ontology-admin")
    for bad in ({"dataset": "no_namespace", "snapshot_id": "1"}, {"dataset": "a.b", "snapshot_id": "abc"}):
        response = client.put(
            "/v1/objects/CommercialDrone/D1", json={"properties": DRONE, "provenance": bad}, headers=admin
        )
        assert response.status_code == 422, bad


def test_put_with_if_none_match_only_creates(client: TestClient, auth: Auth, ontology: None) -> None:
    admin = make_drone(client, auth)
    create_only = {**admin, "If-None-Match": "*"}
    clash = client.put("/v1/objects/CommercialDrone/D1", json={"properties": DRONE}, headers=create_only)
    assert clash.status_code == 409
    fresh = client.put("/v1/objects/CommercialDrone/D2", json={"properties": DRONE}, headers=create_only)
    assert fresh.status_code == 200
    odd = client.put(
        "/v1/objects/CommercialDrone/D3",
        json={"properties": DRONE},
        headers={**admin, "If-None-Match": '"v1"'},
    )
    assert odd.status_code == 422
