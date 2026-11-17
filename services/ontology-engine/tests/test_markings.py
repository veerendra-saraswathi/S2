"""Read-side access control from markings (domain/markings.py, ADR 0010).

A small pack of its own, so the rules are visible in one place:

    Site            unmarked; `notes` is marked `internal`, `coords` marked `internal` + `geo`
    Vault           the whole type is marked `restricted`
    Site --guards--> Vault
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from .conftest import Auth

PACK: dict[str, Any] = {
    "name": "markings-test",
    "version": "0.1.0",
    "object_types": [
        {
            "api_name": "Site",
            "display_name": "Site",
            "properties": [
                {"api_name": "siteId", "display_name": "ID", "data_type": "string", "is_primary_key": True},
                {"api_name": "name", "display_name": "Name", "data_type": "string", "is_title": True},
                {
                    "api_name": "notes",
                    "display_name": "Notes",
                    "data_type": "string",
                    "markings": ["internal"],
                },
                {
                    "api_name": "coords",
                    "display_name": "Coordinates",
                    "data_type": "string",
                    "markings": ["internal", "geo"],
                },
                {
                    "api_name": "load",
                    "display_name": "Load",
                    "data_type": "timeseries",
                    "binding": {"metric": "load"},
                    "markings": ["internal"],
                },
                {
                    "api_name": "temp",
                    "display_name": "Temp",
                    "data_type": "timeseries",
                    "binding": {"metric": "t"},
                },
            ],
        },
        {
            "api_name": "Vault",
            "display_name": "Vault",
            "markings": ["restricted"],
            "properties": [
                {"api_name": "vaultId", "display_name": "ID", "data_type": "string", "is_primary_key": True},
                {"api_name": "contents", "display_name": "Contents", "data_type": "string"},
            ],
        },
    ],
    "link_types": [
        {
            "api_name": "guards",
            "display_name": "guards",
            "reverse_api_name": "guardedBy",
            "reverse_display_name": "guarded by",
            "source_object_type": "Site",
            "target_object_type": "Vault",
            "cardinality": "many_to_many",
        }
    ],
}

ALL = ("ontology-admin", "marking:internal", "marking:geo", "marking:restricted")
SITE = {"name": "North", "notes": "gate code 4417", "coords": "17.4,78.3"}


@pytest.fixture
def world(client: TestClient, auth: Auth) -> None:
    full = auth(*ALL)
    assert client.post("/v1/ontology/packs:apply", json=PACK, headers=full).status_code == 200
    assert client.put("/v1/objects/Site/S1", json={"properties": SITE}, headers=full).status_code == 200
    vault = {"properties": {"contents": "spares"}}
    assert client.put("/v1/objects/Vault/V1", json=vault, headers=full).status_code == 200
    linked = client.post("/v1/objects/Site/S1/links/guards", json={"target_primary_key": "V1"}, headers=full)
    assert linked.status_code == 204, linked.text


def message(response: Any) -> str:
    return response.json()["detail"]["message"]


def props(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    return response.json()["properties"]


# --------------------------------------------------------------------------- reads


def test_cleared_callers_see_everything(client: TestClient, auth: Auth, world: None) -> None:
    viewer = auth("viewer", "marking:internal", "marking:geo")
    assert props(client.get("/v1/objects/Site/S1", headers=viewer)) == {"siteId": "S1", **SITE}


def test_unmarked_callers_lose_marked_properties(client: TestClient, auth: Auth, world: None) -> None:
    viewer = auth("viewer")
    assert props(client.get("/v1/objects/Site/S1", headers=viewer)) == {"siteId": "S1", "name": "North"}
    listed = client.get("/v1/objects/Site", headers=viewer).json()
    assert listed[0]["properties"] == {"siteId": "S1", "name": "North"}


def test_every_marking_must_be_held(client: TestClient, auth: Auth, world: None) -> None:
    partly = auth("viewer", "marking:internal")  # not `geo`
    assert props(client.get("/v1/objects/Site/S1", headers=partly)) == {
        "siteId": "S1",
        "name": "North",
        "notes": "gate code 4417",
    }


def test_a_marked_type_does_not_exist_for_the_uncleared(client: TestClient, auth: Auth, world: None) -> None:
    viewer = auth("viewer", "marking:internal", "marking:geo")
    unknown = client.get("/v1/objects/Nonexistent", headers=viewer)
    for path in ("/v1/objects/Vault", "/v1/objects/Vault/V1"):
        response = client.get(path, headers=viewer)
        assert response.status_code == 404
        assert message(response).replace("Vault", "X") == message(unknown).replace("Nonexistent", "X"), (
            "indistinguishable from a type that does not exist"
        )
    cleared = auth("viewer", "marking:restricted")
    assert props(client.get("/v1/objects/Vault/V1", headers=cleared)) == {
        "vaultId": "V1",
        "contents": "spares",
    }


def test_links_into_a_marked_type_are_hidden_both_ways(client: TestClient, auth: Auth, world: None) -> None:
    viewer = auth("viewer")
    assert client.get("/v1/objects/Site/S1/links/guards", headers=viewer).status_code == 404
    assert client.get("/v1/objects/Vault/V1/links/guardedBy", headers=viewer).status_code == 404

    cleared = auth("viewer", "marking:restricted")
    far = client.get("/v1/objects/Site/S1/links/guards", headers=cleared).json()
    assert [o["primary_key"] for o in far] == ["V1"]
    back = client.get("/v1/objects/Vault/V1/links/guardedBy", headers=cleared).json()
    assert back[0]["properties"] == {"siteId": "S1", "name": "North"}, "marked properties still apply"


def test_a_marked_series_answers_as_if_absent(client: TestClient, auth: Auth, world: None) -> None:
    viewer = auth("viewer")
    hidden = client.get("/v1/objects/Site/S1/timeseries/load", headers=viewer)
    absent = client.get("/v1/objects/Site/S1/timeseries/nothing", headers=viewer)
    assert hidden.status_code == absent.status_code == 404
    assert message(hidden).replace("load", "X") == message(absent).replace("nothing", "X")
    # The test client has no telemetry store: a series the caller may read gets as far as that.
    assert client.get("/v1/objects/Site/S1/timeseries/temp", headers=viewer).status_code == 503
    cleared = auth("viewer", "marking:internal")
    assert client.get("/v1/objects/Site/S1/timeseries/load", headers=cleared).status_code == 503


def test_definitions_stay_readable(client: TestClient, auth: Auth, world: None) -> None:
    names = [t["api_name"] for t in client.get("/v1/ontology/object-types", headers=auth("viewer")).json()]
    assert "Vault" in names


# --------------------------------------------------------------------------- writes


def test_an_uncleared_admin_cannot_write_or_wipe_marked_properties(
    client: TestClient, auth: Auth, world: None
) -> None:
    admin = auth("ontology-admin")
    refused = client.put(
        "/v1/objects/Site/S1", json={"properties": {"name": "N", "notes": "overwrite"}}, headers=admin
    )
    assert refused.status_code == 403
    assert refused.json()["detail"]["properties"] == ["notes"]

    # A replace that leaves out what the admin cannot see keeps it.
    replaced = client.put("/v1/objects/Site/S1", json={"properties": {"name": "Renamed"}}, headers=admin)
    assert props(replaced) == {"siteId": "S1", "name": "Renamed"}
    full = auth("viewer", "marking:internal", "marking:geo")
    assert props(client.get("/v1/objects/Site/S1", headers=full)) == {
        "siteId": "S1",
        **SITE,
        "name": "Renamed",
    }


def test_a_cleared_admin_replace_still_clears(client: TestClient, auth: Auth, world: None) -> None:
    admin = auth(*ALL)
    replaced = client.put("/v1/objects/Site/S1", json={"properties": {"name": "Bare"}}, headers=admin)
    assert props(replaced) == {"siteId": "S1", "name": "Bare"}


def test_an_uncleared_admin_cannot_touch_a_marked_type(client: TestClient, auth: Auth, world: None) -> None:
    admin = auth("ontology-admin")
    assert client.put("/v1/objects/Vault/V2", json={"properties": {}}, headers=admin).status_code == 404
    assert client.delete("/v1/objects/Vault/V1", headers=admin).status_code == 404
    link = client.post("/v1/objects/Site/S1/links/guards", json={"target_primary_key": "V1"}, headers=admin)
    assert link.status_code == 404
    assert props(client.get("/v1/objects/Vault/V1", headers=auth("viewer", "marking:restricted")))


def test_the_primary_key_cannot_be_marked(client: TestClient, auth: Auth) -> None:
    pack = {
        "name": "bad",
        "version": "0.1.0",
        "object_types": [
            {
                "api_name": "Thing",
                "display_name": "Thing",
                "properties": [
                    {
                        "api_name": "thingId",
                        "display_name": "ID",
                        "data_type": "string",
                        "is_primary_key": True,
                        "markings": ["internal"],
                    }
                ],
            }
        ],
    }
    response = client.post("/v1/ontology/packs:apply", json=pack, headers=auth("ontology-admin"))
    assert response.status_code == 422
    assert "primary key cannot carry markings" in response.text


# --------------------------------------------------------------------------- audit


def audit(client: TestClient, headers: dict[str, str], **filters: str) -> list[dict[str, Any]]:
    response = client.get("/v1/audit", params=filters, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_audit_values_follow_the_readers_markings(client: TestClient, auth: Auth, world: None) -> None:
    (site,) = audit(client, auth("auditor"), event_type="object.upserted", resource_type="Site")
    assert site["redacted"] is True
    assert site["resource_id"] == "S1"
    assert site["details"]["after"] == {"name": "North"}
    assert "gate code" not in str(site)

    (full,) = audit(
        client,
        auth("auditor", "marking:internal", "marking:geo"),
        event_type="object.upserted",
        resource_type="Site",
    )
    assert full["redacted"] is False
    assert full["details"]["after"] == SITE


def test_audit_entries_about_a_marked_type_keep_only_their_shape(
    client: TestClient, auth: Auth, world: None
) -> None:
    (vault,) = audit(client, auth("auditor"), event_type="object.upserted", resource_type="Vault")
    assert vault["redacted"] is True
    assert vault["resource_id"] is None
    assert vault["details"] == {"op": "create_object", "redacted": True}

    (link,) = audit(client, auth("auditor"), event_type="link.created")
    assert link["details"] == {"op": "create_link", "redacted": True}
    assert "V1" not in str(link["details"])


def test_redaction_never_breaks_the_chain(client: TestClient, auth: Auth, world: None) -> None:
    verification = client.get("/v1/audit/verify", headers=auth("auditor")).json()
    assert verification["verified"] is True


def test_redaction_reaches_into_action_changes() -> None:
    """Action entries carry a list of change records under `changes`."""
    import uuid

    from s2_common.auth import Principal
    from s2_ontology.db.models import ObjectType, PropertyType
    from s2_ontology.domain.markings import AuditRedactor

    drone = ObjectType(api_name="Drone", markings=[])
    drone.properties = [
        PropertyType(api_name="droneId", markings=[], is_primary_key=True),
        PropertyType(api_name="status", markings=[]),
        PropertyType(api_name="health", markings=["internal"]),
    ]
    principal = Principal(subject="a", tenant_id=uuid.uuid4(), roles=frozenset({"auditor"}))
    redactor = AuditRedactor(principal, {"Drone": drone}.get)
    details = {
        "action": "groundDrone",
        "parameters": {"droneId": "D1"},
        "changes": [
            {
                "op": "modify_object",
                "object": "Drone/D1",
                "before": {"status": "READY", "health": 0.9},
                "after": {"status": "GROUNDED", "health": 0.1},
            },
        ],
    }
    out, redacted = redactor.redact(details)
    assert redacted
    assert out["changes"][0]["before"] == {"status": "READY"}
    assert out["changes"][0]["after"] == {"status": "GROUNDED"}
    assert out["changes"][0]["redacted"] is True
    assert out["parameters"] == {"droneId": "D1"}
    assert details["changes"][0]["before"]["health"] == 0.9, "the input is not modified"
