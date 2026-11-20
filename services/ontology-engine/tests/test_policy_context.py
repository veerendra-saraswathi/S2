"""`policy_context`: objects an action's parameters name, shown to its policy beside the target."""

from __future__ import annotations

import copy
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from s2_common.auth import TokenVerifier
from s2_common.opa import Decision, OpaClient
from s2_ontology.app import create_app

from .conftest import Auth, _settings


class RecordingOpa(OpaClient):
    """The real policy engine, keeping every input it was asked to decide on."""

    def __init__(self, url: str) -> None:
        super().__init__(url)
        self.inputs: list[dict[str, Any]] = []

    def decide(self, policy_path: str, input_doc: dict[str, Any]) -> Decision:
        self.inputs.append(input_doc)
        return super().decide(policy_path, input_doc)


@pytest.fixture(scope="module")
def recorded(database: dict[str, str], opa_url: str, verifier: TokenVerifier) -> Iterator[tuple]:
    opa = RecordingOpa(opa_url)
    app = create_app(_settings(database, opa_url), verifier=verifier, opa=opa)
    with TestClient(app) as test_client:
        yield test_client, opa


def _key(name: str) -> dict[str, Any]:
    return {"api_name": name, "display_name": name, "data_type": "string", "is_primary_key": True}


PACK: dict[str, Any] = {
    "name": "context-probe",
    "version": "0.1.0",
    "object_types": [
        {
            "api_name": "Build",
            "display_name": "Build",
            "properties": [
                _key("buildId"),
                {"api_name": "status", "display_name": "S", "data_type": "string"},
            ],
        },
        {
            "api_name": "Run",
            "display_name": "Run",
            "properties": [
                _key("runId"),
                {"api_name": "verdict", "display_name": "V", "data_type": "string"},
            ],
        },
    ],
    "link_types": [
        {
            "api_name": "testsBuild",
            "display_name": "Tests build",
            "reverse_api_name": "testRuns",
            "reverse_display_name": "Test runs",
            "source_object_type": "Run",
            "target_object_type": "Build",
            "cardinality": "many_to_one",
        }
    ],
    "action_types": [
        {
            "api_name": "promoteBuild",
            "display_name": "Promote build",
            "target_object_type": "Build",
            "parameters": {
                "type": "object",
                "required": ["buildId", "runId"],
                "properties": {"buildId": {"type": "string"}, "runId": {"type": "string"}},
            },
            "edits": [
                {
                    "op": "modify_object",
                    "object_type": "Build",
                    "target": "{{ params.buildId }}",
                    "assign": {"status": "PROMOTED"},
                }
            ],
            "policy_context": {
                "run": {"object_type": "Run", "key": "{{ params.runId }}", "links": ["testsBuild"]},
                "builds": {"object_type": "Build", "key": "{{ params.buildId }}", "links": ["testRuns"]},
            },
        }
    ],
}


def _apply(client: TestClient, auth: Auth, pack: dict[str, Any]) -> Any:
    return client.post("/v1/ontology/packs:apply", json=pack, headers=auth("ontology-admin"))


def test_the_policy_sees_the_named_objects_and_their_links(recorded: tuple, auth: Auth) -> None:
    client, opa = recorded
    admin = auth("ontology-admin")
    assert _apply(client, auth, PACK).status_code == 200
    client.put("/v1/objects/Build/B1", json={"properties": {"status": "NEW"}}, headers=admin)
    client.put("/v1/objects/Run/R1", json={"properties": {"verdict": "FAIL"}}, headers=admin)
    assert (
        client.post(
            "/v1/objects/Run/R1/links/testsBuild", json={"target_primary_key": "B1"}, headers=admin
        ).status_code
        == 204
    )

    opa.inputs.clear()
    response = client.post(
        "/v1/actions/promoteBuild/apply",
        json={"parameters": {"buildId": "B1", "runId": "R1"}},
        headers=auth("operator"),
    )
    assert response.status_code == 200, response.text
    context = opa.inputs[-1]["context"]
    assert context["run"] == {
        "object_type": "Run",
        "primary_key": "R1",
        "properties": {"runId": "R1", "verdict": "FAIL"},
        "links": {"testsBuild": ["B1"]},
    }
    assert context["builds"]["links"] == {"testRuns": ["R1"]}  # reverse names work too


def test_a_missing_object_is_null(recorded: tuple, auth: Auth) -> None:
    client, opa = recorded
    assert _apply(client, auth, PACK).status_code == 200
    client.put("/v1/objects/Build/B2", json={"properties": {"status": "NEW"}}, headers=auth("ontology-admin"))

    opa.inputs.clear()
    client.post(
        "/v1/actions/promoteBuild/apply",
        json={"parameters": {"buildId": "B2", "runId": "NO-SUCH-RUN"}},
        headers=auth("operator"),
    )
    assert opa.inputs[-1]["context"]["run"] is None


def test_an_action_without_context_gets_an_empty_one(recorded: tuple, auth: Auth) -> None:
    client, opa = recorded
    pack = copy.deepcopy(PACK)
    del pack["action_types"][0]["policy_context"]
    assert _apply(client, auth, pack).status_code == 200
    client.put("/v1/objects/Build/B3", json={"properties": {"status": "NEW"}}, headers=auth("ontology-admin"))

    opa.inputs.clear()
    client.post(
        "/v1/actions/promoteBuild/apply",
        json={"parameters": {"buildId": "B3", "runId": "R"}},
        headers=auth("operator"),
    )
    assert opa.inputs[-1]["context"] == {}


@pytest.mark.parametrize(
    ("ref", "message"),
    [
        ({"object_type": "Nope", "key": "{{ params.runId }}"}, "Nope"),
        ({"object_type": "Run", "key": "{{ params.runId }}", "links": ["nope"]}, "has no link 'nope'"),
        ({"object_type": "Run", "key": "{{ params.other }}"}, "undeclared parameters ['other']"),
    ],
)
def test_a_context_that_could_never_resolve_is_refused(
    client: TestClient, auth: Auth, ref: dict[str, Any], message: str
) -> None:
    pack = copy.deepcopy(PACK)
    pack["action_types"][0]["policy_context"] = {"run": ref}
    response = _apply(client, auth, pack)
    assert response.status_code == 422, response.text
    assert message in response.text


def test_the_context_is_part_of_the_action_type(client: TestClient, auth: Auth) -> None:
    assert _apply(client, auth, PACK).status_code == 200
    listed = client.get("/v1/ontology/action-types", headers=auth("viewer"))
    assert listed.status_code == 200, listed.text
    (action,) = [a for a in listed.json() if a["api_name"] == "promoteBuild"]
    assert action["policy_context"]["run"]["links"] == ["testsBuild"]
