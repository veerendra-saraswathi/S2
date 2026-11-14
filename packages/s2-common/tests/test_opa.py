from typing import Any

import httpx
import pytest

from s2_common.opa import OpaClient, PolicyError


def client_returning(body: Any = None, status: int = 200, raises: Exception | None = None) -> OpaClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if raises:
            raise raises
        return httpx.Response(status, json=body)

    transport = httpx.MockTransport(handler)
    return OpaClient("http://opa:8181", client=httpx.Client(base_url="http://opa:8181", transport=transport))


def test_allow() -> None:
    client = client_returning({"result": {"allow": True, "deny": []}, "decision_id": "d-1"})
    decision = client.decide("s2/actions/standard", {})
    assert decision.allow and decision.decision_id == "d-1" and decision.reasons == ()


def test_any_deny_reason_vetoes_an_allow() -> None:
    body = {"result": {"allow": True, "deny": ["the drone is already grounded"]}}
    decision = client_returning(body).decide("s2/actions/ground_drone", {})
    assert not decision.allow
    assert decision.reasons == ("the drone is already grounded",)


@pytest.mark.parametrize(
    "body",
    [
        {},  # no policy at that path: OPA returns no result
        {"result": {}},  # package loaded, allow undefined
        {"result": {"allow": "yes"}},  # truthy but not `true`
        {"result": True},  # the caller pointed at a rule instead of a package
        {"result": {"allow": True, "deny": "everything"}},  # deny is not a set
        ["unexpected"],
    ],
)
def test_fails_closed_on_missing_or_malformed_result(body: Any) -> None:
    decision = client_returning(body).decide("s2/actions/standard", {})
    assert not decision.allow
    assert decision.reasons


def test_an_unreachable_engine_is_an_error_not_a_denial() -> None:
    with pytest.raises(PolicyError):
        client_returning(raises=httpx.ConnectError("refused")).decide("s2/actions/standard", {})
    with pytest.raises(PolicyError):
        client_returning({"code": "internal_error"}, status=500).decide("s2/actions/standard", {})


@pytest.mark.parametrize("path", ["../v1/policies", "s2/authz?pretty", "S2/Actions", "s2//actions", ""])
def test_rejects_suspicious_policy_paths(path: str) -> None:
    with pytest.raises(PolicyError):
        client_returning({"result": {"allow": True}}).decide(path, {})


def test_health() -> None:
    assert client_returning({}).healthy()
    assert not client_returning({}, status=500).healthy()
    assert not client_returning(raises=httpx.ConnectError("refused")).healthy()
