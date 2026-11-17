"""Governed actions end to end (real OPA, real policies) and the audit chain."""

from __future__ import annotations

import copy
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from .conftest import Auth, MakeToken, load_pack

GROUND = "/v1/actions/groundDrone/apply"
RETURN = "/v1/actions/returnDroneToService/apply"


def _status(client: TestClient, auth: Auth, drone: str) -> str:
    return client.get(f"/v1/objects/CommercialDrone/{drone}", headers=auth("viewer")).json()["properties"][
        "status"
    ]


def _audit(client: TestClient, auth: Auth, **filters: str) -> list[dict]:
    return client.get("/v1/audit", params=filters, headers=auth("auditor")).json()


# --------------------------------------------------------------------------- #
# Immediate actions
# --------------------------------------------------------------------------- #


def test_operator_grounds_a_drone(client: TestClient, auth: Auth, drone: str) -> None:
    body = {"parameters": {"droneId": drone, "reason": "actuator health below threshold"}}
    response = client.post(GROUND, json=body, headers=auth("operator"))
    assert response.status_code == 200, response.text
    execution = response.json()

    assert execution["status"] == "succeeded"
    assert execution["actor_id"] == "user-operator"
    assert execution["policy_decision_id"]  # OPA's decision log id
    # The write-back is queued, not sent: delivery belongs to the action executor.
    assert [(w["connector"], w["operation"], w["status"]) for w in execution["writebacks"]] == [
        ("samanvaya-fleet", "ground_vehicle", "pending")
    ]

    after = client.get(f"/v1/objects/CommercialDrone/{drone}", headers=auth("viewer")).json()
    assert after["properties"]["status"] == "GROUNDED"
    assert after["properties"]["groundedReason"] == "actuator health below threshold"

    entry = _audit(client, auth, event_type="action.executed")[0]
    assert entry["resource_id"] == execution["id"]
    assert entry["policy_decision_id"] == execution["policy_decision_id"]
    assert entry["details"]["changes"] == [
        {
            "op": "modify_object",
            "object": f"CommercialDrone/{drone}",
            "before": {"status": "READY", "groundedReason": None},
            "after": {"status": "GROUNDED", "groundedReason": "actuator health below threshold"},
        }
    ]


def test_policy_denials_change_nothing_and_are_audited(client: TestClient, auth: Auth, drone: str) -> None:
    body = {"parameters": {"droneId": drone, "reason": "looks wobbly to me"}}

    denied = client.post(GROUND, json=body, headers=auth("viewer"))
    assert denied.status_code == 403
    detail = denied.json()["detail"]
    assert detail["reasons"] == ["requesting an action needs the operator role"]
    assert _status(client, auth, drone) == "READY"

    entry = _audit(client, auth, event_type="action.denied")[0]
    assert entry["outcome"] == "denied" and entry["actor_id"] == "user-viewer"
    assert entry["details"]["reasons"] == detail["reasons"]
    assert entry["policy_decision_id"] == detail["decision_id"]

    # Business rules live in the same policy: a grounded drone cannot be grounded again.
    assert client.post(GROUND, json=body, headers=auth("operator")).status_code == 200
    again = client.post(GROUND, json=body, headers=auth("operator"))
    assert again.status_code == 403
    assert again.json()["detail"]["reasons"] == ["the drone is already grounded"]

    unknown = {"parameters": {"droneId": "DRN-404", "reason": "does not exist"}}
    ghost = client.post(GROUND, json=unknown, headers=auth("operator"))
    assert ghost.status_code == 403
    assert ghost.json()["detail"]["reasons"] == ["the drone does not exist"]


def test_state_dependent_rule_needs_a_supervisor(client: TestClient, auth: Auth, ontology: None) -> None:
    flying = {"properties": {"callSign": "Garuda-7", "status": "IN_MISSION"}}
    client.put("/v1/objects/CommercialDrone/DRN-0007", json=flying, headers=auth("ontology-admin"))
    body = {"parameters": {"droneId": "DRN-0007", "reason": "weather closing in"}}

    refused = client.post(GROUND, json=body, headers=auth("operator"))
    assert refused.status_code == 403
    assert "fleet-supervisor" in refused.json()["detail"]["reasons"][0]
    assert client.post(GROUND, json=body, headers=auth("operator", "fleet-supervisor")).status_code == 200


def test_parameters_are_validated_before_policy(client: TestClient, auth: Auth, drone: str) -> None:
    operator = auth("operator")
    short = client.post(GROUND, json={"parameters": {"droneId": drone, "reason": "bad"}}, headers=operator)
    assert short.status_code == 422
    assert short.json()["detail"]["errors"][0]["path"] == "reason"
    extra = {"parameters": {"droneId": drone, "reason": "long enough", "force": True}}
    assert client.post(GROUND, json=extra, headers=operator).status_code == 422
    assert client.post(GROUND, json={"parameters": {}}, headers=operator).status_code == 422
    assert (
        client.post("/v1/actions/launchRocket/apply", json={"parameters": {}}, headers=operator).status_code
        == 404
    )
    assert _status(client, auth, drone) == "READY"
    assert _audit(client, auth, event_type="action.executed") == []


def test_idempotency_key_makes_retries_safe(client: TestClient, auth: Auth, drone: str) -> None:
    headers = {**auth("operator"), "Idempotency-Key": f"retry-{uuid.uuid4()}"}
    body = {"parameters": {"droneId": drone, "reason": "motor vibration alarm"}}

    first = client.post(GROUND, json=body, headers=headers)
    second = client.post(GROUND, json=body, headers=headers)
    assert first.status_code == second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert "Idempotent-Replay" not in first.headers and second.headers["Idempotent-Replay"] == "true"
    assert len(_audit(client, auth, event_type="action.executed")) == 1

    other_action = {"parameters": {"workOrderId": "WO-1", "title": "Replace motor"}}
    clash = client.post("/v1/actions/createWorkOrder/apply", json=other_action, headers=headers)
    assert clash.status_code == 409


def test_an_action_that_cannot_apply_fails_atomically(client: TestClient, auth: Auth, ontology: None) -> None:
    operator = auth("operator")
    url = "/v1/actions/createWorkOrder/apply"
    body = {"parameters": {"workOrderId": "WO-1", "title": "Replace insulator", "priority": "HIGH"}}

    created = client.post(url, json=body, headers=operator)
    assert created.status_code == 200, created.text
    order = client.get("/v1/objects/WorkOrder/WO-1", headers=operator).json()["properties"]
    assert order == {
        "workOrderId": "WO-1",
        "title": "Replace insulator",
        "status": "OPEN",
        "priority": "HIGH",
    }

    duplicate = client.post(url, json=body, headers=operator)
    assert duplicate.status_code == 409
    execution = duplicate.json()["detail"]["execution"]
    assert execution["status"] == "failed" and "already exists" in execution["error"]["message"]
    assert execution["writebacks"] == []  # nothing queued for a failed action

    failed = _audit(client, auth, event_type="action.failed")
    assert len(failed) == 1 and failed[0]["outcome"] == "error"


# --------------------------------------------------------------------------- #
# Approval
# --------------------------------------------------------------------------- #


def _grounded(client: TestClient, auth: Auth, drone: str) -> None:
    body = {"parameters": {"droneId": drone, "reason": "inspection after hard landing"}}
    assert client.post(GROUND, json=body, headers=auth("operator")).status_code == 200


def test_four_eyes_approval(client: TestClient, auth: Auth, drone: str) -> None:
    _grounded(client, auth, drone)

    requested = client.post(RETURN, json={"parameters": {"droneId": drone}}, headers=auth("operator"))
    assert requested.status_code == 202
    execution = requested.json()
    assert execution["status"] == "pending_approval" and execution["writebacks"] == []
    assert _status(client, auth, drone) == "GROUNDED"  # nothing happens until approved
    approve = f"/v1/action-executions/{execution['id']}/approve"

    # An operator who is not an approver cannot approve.
    plain = client.post(approve, headers=auth("operator", sub="someone-else"))
    assert plain.status_code == 403
    assert plain.json()["detail"]["reasons"] == ["approving an action needs the approver role"]

    # The requester cannot approve their own request, even holding the approver role.
    own = client.post(approve, headers=auth("operator", "approver", sub="user-operator"))
    assert own.status_code == 403
    assert own.json()["detail"]["reasons"] == ["the requester cannot approve their own request"]
    assert _status(client, auth, drone) == "GROUNDED"

    approved = client.post(approve, headers=auth("approver"))
    assert approved.status_code == 200, approved.text
    done = approved.json()
    assert done["status"] == "succeeded" and done["approved_by"] == "user-approver"
    assert [w["operation"] for w in done["writebacks"]] == ["release_vehicle"]

    after = client.get(f"/v1/objects/CommercialDrone/{drone}", headers=auth("viewer")).json()["properties"]
    assert after["status"] == "READY" and "groundedReason" not in after

    assert client.post(approve, headers=auth("approver")).status_code == 409  # already decided
    events = [e["event_type"] for e in _audit(client, auth, resource_id=execution["id"])]
    assert events == ["action.executed", "action.requested"]  # newest first


def test_rejection(client: TestClient, auth: Auth, drone: str) -> None:
    _grounded(client, auth, drone)
    execution = client.post(RETURN, json={"parameters": {"droneId": drone}}, headers=auth("operator")).json()
    reject = f"/v1/action-executions/{execution['id']}/reject"

    assert (
        client.post(reject, json={"reason": "not yet inspected"}, headers=auth("operator")).status_code == 403
    )
    rejected = client.post(reject, json={"reason": "not yet inspected"}, headers=auth("approver"))
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert _status(client, auth, drone) == "GROUNDED"
    fetched = client.get(f"/v1/action-executions/{execution['id']}", headers=auth("viewer")).json()
    assert fetched["error"] == {"rejected_by": "user-approver", "reason": "not yet inspected"}


def test_policy_is_re_evaluated_at_approval(client: TestClient, auth: Auth, drone: str) -> None:
    """Approval is checked against the world as it is now, not as it was when requested."""
    _grounded(client, auth, drone)
    execution = client.post(RETURN, json={"parameters": {"droneId": drone}}, headers=auth("operator")).json()
    client.delete(f"/v1/objects/CommercialDrone/{drone}", headers=auth("ontology-admin"))

    approved = client.post(f"/v1/action-executions/{execution['id']}/approve", headers=auth("approver"))
    assert approved.status_code == 409
    assert approved.json()["detail"]["execution"]["status"] == "failed"


# --------------------------------------------------------------------------- #
# Agents
# --------------------------------------------------------------------------- #


def test_agents_propose_and_humans_decide(
    client: TestClient, auth: Auth, drone: str, make_token: MakeToken, tenant: uuid.UUID
) -> None:
    def agent(*roles: str, sub: str, acting_for: str) -> dict[str, str]:
        token = make_token(*roles, tenant=tenant, sub=sub, s2_actor_type="agent", s2_on_behalf_of=acting_for)
        return {"Authorization": f"Bearer {token}"}

    _grounded(client, auth, drone)
    copilot = agent("operator", sub="copilot", acting_for="user-operator")

    # An agent cannot run an action that executes immediately...
    order = {"parameters": {"workOrderId": "WO-9", "title": "Inspect rotor"}, "rationale": "rotor vibration"}
    blocked = client.post("/v1/actions/createWorkOrder/apply", json=order, headers=copilot)
    assert blocked.status_code == 403
    assert (
        "agents may only propose actions that require human approval" in blocked.json()["detail"]["reasons"]
    )
    assert client.get("/v1/objects/WorkOrder/WO-9", headers=auth("viewer")).status_code == 404

    # ...and must always say why it proposes something.
    silent = client.post(RETURN, json={"parameters": {"droneId": drone}}, headers=copilot)
    assert silent.status_code == 422
    assert "rationale" in silent.json()["detail"]["message"]

    # It may propose an action that is gated and open to agents. It waits for a human.
    reason = "actuator health recovered to 0.97 after the replacement on 17 Nov"
    body = {"parameters": {"droneId": drone}, "rationale": reason}
    proposed = client.post(RETURN, json=body, headers=copilot)
    assert proposed.status_code == 202, proposed.text
    execution = proposed.json()
    assert execution["actor_type"] == "agent" and execution["on_behalf_of"] == "user-operator"
    assert execution["rationale"] == reason
    assert _status(client, auth, drone) == "GROUNDED"

    requested = _audit(client, auth, event_type="action.requested")[0]
    assert requested["actor_type"] == "agent" and requested["on_behalf_of"] == "user-operator"
    assert requested["details"]["rationale"] == reason
    listed = client.get(
        "/v1/action-executions", params={"status": "pending_approval"}, headers=auth("approver")
    )
    assert listed.json()[0]["rationale"] == reason

    # An agent cannot be the approver, whatever roles its token carries.
    approve = f"/v1/action-executions/{execution['id']}/approve"
    by_agent = client.post(approve, headers=agent("approver", sub="copilot-2", acting_for="user-approver"))
    assert by_agent.status_code == 403
    assert by_agent.json()["detail"]["reasons"] == ["agents cannot approve actions"]

    assert client.post(approve, headers=auth("approver")).status_code == 200
    assert _status(client, auth, drone) == "READY"


def test_an_edit_that_would_clear_a_required_property_fails_whole(
    client: TestClient, auth: Auth, drone: str
) -> None:
    pack = copy.deepcopy(load_pack("core-fleet"))
    ground = next(a for a in pack["action_types"] if a["api_name"] == "groundDrone")
    ground["edits"][0]["assign"]["callSign"] = None  # callSign is required
    assert (
        client.post("/v1/ontology/packs:apply", json=pack, headers=auth("ontology-admin")).status_code == 200
    )

    body = {"parameters": {"droneId": drone, "reason": "would also erase the call sign"}}
    failed = client.post(GROUND, json=body, headers=auth("operator"))
    assert failed.status_code == 409
    assert "required property is missing" in failed.json()["detail"]["execution"]["error"]["message"]
    after = client.get(f"/v1/objects/CommercialDrone/{drone}", headers=auth("viewer")).json()["properties"]
    assert after["status"] == "READY" and after["callSign"] == "Garuda-1"  # not even the status changed


# --------------------------------------------------------------------------- #
# Policy engine outage
# --------------------------------------------------------------------------- #


def test_no_policy_engine_means_no_action(
    client: TestClient, client_without_opa: TestClient, auth: Auth, drone: str
) -> None:
    body = {"parameters": {"droneId": drone, "reason": "opa is down, try anyway"}}
    response = client_without_opa.post(GROUND, json=body, headers=auth("operator"))
    assert response.status_code == 503
    assert _status(client, auth, drone) == "READY"  # fail closed
    assert client_without_opa.get("/readyz").status_code == 503
    errors = _audit(client, auth, event_type="action.policy_error")
    assert len(errors) == 1 and errors[0]["outcome"] == "error"


# --------------------------------------------------------------------------- #
# Audit chain
# --------------------------------------------------------------------------- #


def test_audit_is_restricted_and_verifiable(client: TestClient, auth: Auth, drone: str) -> None:
    _grounded(client, auth, drone)
    assert client.get("/v1/audit", headers=auth("operator")).status_code == 403
    assert client.get("/v1/audit/verify", headers=auth("viewer")).status_code == 403

    entries = _audit(client, auth)
    assert [e["event_type"] for e in entries] == ["action.executed", "object.upserted"] + [
        "ontology.pack.applied"
    ] * 2
    assert [e["seq"] for e in entries] == [4, 3, 2, 1]
    assert all(len(e["entry_hash"]) == 64 for e in entries)

    verification = client.get("/v1/audit/verify", headers=auth("auditor")).json()
    assert verification == {"entries": 4, "verified": True, "first_bad_seq": None}


def test_tampering_with_history_is_detected(
    client: TestClient, auth: Auth, drone: str, tenant: uuid.UUID, owner_engine: Engine
) -> None:
    _grounded(client, auth, drone)
    verify = lambda: client.get("/v1/audit/verify", headers=auth("auditor")).json()  # noqa: E731
    assert verify()["verified"] is True

    # The application role cannot rewrite the log at all...
    app_engine = client.app.state.engine
    for statement in ("UPDATE ontology.audit_log SET actor_id = 'nobody'", "DELETE FROM ontology.audit_log"):
        with app_engine.connect() as connection:
            connection.execute(text("SELECT set_config('s2.tenant_id', :t, true)"), {"t": str(tenant)})
            try:
                connection.execute(text(statement))
                raise AssertionError("the application role rewrote the audit log")
            except Exception as exc:
                assert "permission denied" in str(exc)

    # ...and a superuser who disables the guard trigger and edits an entry is found out.
    with owner_engine.begin() as connection:
        connection.execute(text("ALTER TABLE ontology.audit_log DISABLE TRIGGER audit_log_no_rewrite"))
        connection.execute(
            text("UPDATE ontology.audit_log SET details = '{}' WHERE tenant_id = :t AND seq = 2"),
            {"t": tenant},
        )
        connection.execute(text("ALTER TABLE ontology.audit_log ENABLE TRIGGER audit_log_no_rewrite"))
    assert verify() == {"entries": 2, "verified": False, "first_bad_seq": 2}


def test_executions_are_listed_newest_first_and_filtered(client: TestClient, auth: Auth, drone: str) -> None:
    _grounded(client, auth, drone)
    pending = client.post(RETURN, json={"parameters": {"droneId": drone}}, headers=auth("operator")).json()

    queue = client.get(
        "/v1/action-executions", params={"status": "pending_approval"}, headers=auth("approver")
    )
    assert queue.status_code == 200
    assert [e["id"] for e in queue.json()] == [pending["id"]]

    every = client.get("/v1/action-executions", headers=auth("viewer")).json()
    assert [e["action"] for e in every] == ["returnDroneToService", "groundDrone"], "newest first"
    grounds = client.get(
        "/v1/action-executions", params={"action": "groundDrone"}, headers=auth("viewer")
    ).json()
    assert [e["action"] for e in grounds] == ["groundDrone"]
    assert (
        client.get("/v1/action-executions", params={"status": "bogus"}, headers=auth("viewer")).status_code
        == 422
    )
    other = client.get("/v1/action-executions", headers=auth("viewer", tenant_id=uuid.uuid4())).json()
    assert other == [], "another tenant sees nothing"


def test_the_person_an_agent_acts_for_cannot_approve_its_proposal(
    client: TestClient, auth: Auth, drone: str, make_token: MakeToken, tenant: uuid.UUID
) -> None:
    _grounded(client, auth, drone)
    token = make_token(
        "operator", tenant=tenant, sub="svc-agent", s2_actor_type="agent", s2_on_behalf_of="supervisor"
    )
    body = {"parameters": {"droneId": drone}, "rationale": "fixed"}
    proposed = client.post(RETURN, json=body, headers={"Authorization": f"Bearer {token}"}).json()
    approve = f"/v1/action-executions/{proposed['id']}/approve"

    supervisor = make_token("approver", tenant=tenant, sub="user-supervisor", preferred_username="supervisor")
    own = client.post(approve, headers={"Authorization": f"Bearer {supervisor}"})
    assert own.status_code == 403
    assert own.json()["detail"]["reasons"] == ["the person an agent acts for cannot approve its proposal"]
    assert client.post(approve, headers=auth("approver")).status_code == 200


def test_a_person_may_give_a_rationale_too(client: TestClient, auth: Auth, drone: str) -> None:
    body = {"parameters": {"droneId": drone, "reason": "rotor noise"}, "rationale": "  heard it on landing  "}
    done = client.post(GROUND, json=body, headers=auth("operator")).json()
    assert done["rationale"] == "heard it on landing"
    too_long = {**body, "rationale": "x" * 4001}
    assert client.post(GROUND, json=too_long, headers=auth("operator")).status_code == 422
