"""The Maximo and SAP PM adapters, against fakes of each system's API (no network)."""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest

from s2_executor.cmms import MaximoConnector, SapPmConnector
from s2_executor.connectors import PermanentError, TransientError, build_connectors
from s2_executor.settings import ExecutorSettings, MaximoConfig, SapPmConfig

KEY = uuid.uuid4()
WORK_ORDER = {"external_id": "WO-17", "title": "Replace actuator on DRN-0003", "priority": "HIGH"}


# --------------------------------------------------------------------------- Maximo


class FakeMaximo:
    def __init__(self) -> None:
        self.work_orders: list[dict[str, Any]] = []
        self.requests: list[httpx.Request] = []
        self.fail_next: int | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("apikey") != "secret":
            return httpx.Response(401, json={"Error": {"message": "BMXAA7901E"}})
        assert request.url.path == "/maximo/api/os/mxapiwo"
        assert request.url.params["lean"] == "1"
        if request.method == "GET":
            where = request.url.params["oslc.where"]
            members = [
                {"wonum": w["wonum"]}
                for w in self.work_orders
                if f'externalrefid="{w["externalrefid"]}"' in where and f'siteid="{w["siteid"]}"' in where
            ]
            return httpx.Response(200, json={"member": members})
        if self.fail_next is not None:
            status, self.fail_next = self.fail_next, None
            return httpx.Response(status, json={"Error": {"message": "BMXAA4195E - required field"}})
        body = json.loads(request.content)
        record = {**body, "wonum": f"10{len(self.work_orders) + 1:02d}"}
        self.work_orders.append(record)
        return httpx.Response(201, json={"wonum": record["wonum"]})


def maximo(fake: FakeMaximo, **overrides: Any) -> MaximoConnector:
    config = MaximoConfig(
        type="maximo", base_url="https://maximo.test/", api_key="secret", site_id="HYD", **overrides
    )
    client = httpx.Client(
        base_url=config.base_url, transport=httpx.MockTransport(fake), headers={"apikey": "secret"}
    )
    return MaximoConnector("cmms", config, client=client)


def test_maximo_creates_a_work_order() -> None:
    fake = FakeMaximo()
    maximo(fake, work_type="CM").deliver("create_work_order", WORK_ORDER, KEY)
    (made,) = fake.work_orders
    assert made["siteid"] == "HYD"
    assert made["description"] == "Replace actuator on DRN-0003"
    assert made["externalrefid"] == "WO-17"
    assert made["wopriority"] == 2
    assert made["worktype"] == "CM"
    post = fake.requests[-1]
    assert post.method == "POST" and post.headers["properties"] == "wonum"


def test_maximo_a_retry_finds_the_work_order_instead_of_making_another() -> None:
    fake = FakeMaximo()
    connector = maximo(fake)
    connector.deliver("create_work_order", WORK_ORDER, KEY)
    connector.deliver("create_work_order", WORK_ORDER, KEY)  # the executor retried after a lost response
    assert len(fake.work_orders) == 1
    assert connector.find("WO-17") == "1001"
    assert connector.find("WO-99") is None


def test_maximo_long_titles_keep_the_full_text() -> None:
    fake = FakeMaximo()
    title = "x" * 150
    maximo(fake).deliver("create_work_order", {**WORK_ORDER, "title": title}, KEY)
    made = fake.work_orders[0]
    assert len(made["description"]) == 100
    assert made["description_longdescription"] == title


def test_maximo_errors_are_classified() -> None:
    fake = FakeMaximo()
    fake.fail_next = 400
    with pytest.raises(PermanentError, match="BMXAA4195E"):
        maximo(fake).deliver("create_work_order", WORK_ORDER, KEY)
    fake.fail_next = 503
    with pytest.raises(TransientError, match="503"):
        maximo(fake).deliver("create_work_order", WORK_ORDER, KEY)


def test_maximo_a_wrong_key_is_permanent() -> None:
    fake = FakeMaximo()
    config = MaximoConfig(type="maximo", base_url="https://maximo.test", api_key="wrong", site_id="HYD")
    client = httpx.Client(
        base_url=config.base_url, transport=httpx.MockTransport(fake), headers={"apikey": "wrong"}
    )
    with pytest.raises(PermanentError, match="401"):
        MaximoConnector("cmms", config, client=client).deliver("create_work_order", WORK_ORDER, KEY)


# --------------------------------------------------------------------------- SAP PM


class FakeSap:
    """CSRF tokens are bound to a session cookie; tokens can be expired on demand."""

    def __init__(self, *, reference_field: str | None = None) -> None:
        self.notifications: list[dict[str, Any]] = []
        self.requests: list[httpx.Request] = []
        self.sessions: dict[str, str] = {}
        self.reference_field = reference_field

    def expire_tokens(self) -> None:
        self.sessions.clear()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.url.path == "/sap/opu/odata/sap/API_MAINTNOTIFICATION/MaintenanceNotification"
        assert request.url.params.get("sap-client") == "100"
        if not request.headers.get("authorization", "").startswith("Basic "):
            return httpx.Response(401)
        if request.method == "GET":
            if request.headers.get("x-csrf-token") == "Fetch":
                session, token = uuid.uuid4().hex, uuid.uuid4().hex
                self.sessions[session] = token
                return httpx.Response(
                    200,
                    json={"d": {"results": []}},
                    headers={"x-csrf-token": token, "set-cookie": f"SAP_SESSIONID={session}; Path=/"},
                )
            flt = request.url.params["$filter"]
            results = [
                {"MaintenanceNotification": n["MaintenanceNotification"]}
                for n in self.notifications
                if self.reference_field
                and f"{self.reference_field} eq '{n.get(self.reference_field)}'" == flt
            ]
            return httpx.Response(200, json={"d": {"results": results}})
        cookie = request.headers.get("cookie", "")
        session = cookie.split("SAP_SESSIONID=")[-1].split(";")[0] if "SAP_SESSIONID=" in cookie else None
        if session is None or self.sessions.get(session) != request.headers.get("x-csrf-token"):
            return httpx.Response(
                403, text="CSRF token validation failed", headers={"x-csrf-token": "Required"}
            )
        body = json.loads(request.content)
        record = {**body, "MaintenanceNotification": f"1000{len(self.notifications) + 1:04d}"}
        self.notifications.append(record)
        return httpx.Response(201, json={"d": record})


def sap(fake: FakeSap, **overrides: Any) -> SapPmConnector:
    config = SapPmConfig(
        type="sap-pm",
        base_url="https://s4.test",
        username="S2_COMM",
        password="pw",
        sap_client="100",
        **overrides,
    )
    client = httpx.Client(base_url=config.base_url + config.service_path, transport=httpx.MockTransport(fake))
    return SapPmConnector("cmms", config, client=client)


def test_sap_creates_a_notification_after_the_csrf_handshake() -> None:
    fake = FakeSap()
    sap(fake, notification_type="M2").deliver("create_work_order", WORK_ORDER, KEY)
    (made,) = fake.notifications
    assert made["NotificationText"] == "Replace actuator on DRN-0003"
    assert made["NotificationType"] == "M2"
    assert made["MaintPriority"] == "2"
    assert [r.method for r in fake.requests] == ["GET", "POST"], "fetch a token, then create"


def test_sap_texts_are_cut_to_forty_characters() -> None:
    fake = FakeSap()
    sap(fake).deliver("create_work_order", {**WORK_ORDER, "title": "y" * 60}, KEY)
    assert fake.notifications[0]["NotificationText"] == "y" * 40


def test_sap_an_expired_token_is_refetched_once() -> None:
    fake = FakeSap()
    connector = sap(fake)
    connector.deliver("create_work_order", WORK_ORDER, KEY)
    fake.expire_tokens()
    connector.deliver("create_work_order", {**WORK_ORDER, "external_id": "WO-18"}, KEY)
    assert len(fake.notifications) == 2
    assert [r.method for r in fake.requests] == ["GET", "POST", "POST", "GET", "POST"]


def test_sap_with_a_reference_field_a_retry_creates_nothing() -> None:
    fake = FakeSap(reference_field="YY1_S2WorkOrder_NOT")
    connector = sap(fake, reference_field="YY1_S2WorkOrder_NOT")
    connector.deliver("create_work_order", WORK_ORDER, KEY)
    connector.deliver("create_work_order", WORK_ORDER, KEY)
    assert len(fake.notifications) == 1
    assert fake.notifications[0]["YY1_S2WorkOrder_NOT"] == "WO-17"


def test_sap_needs_credentials() -> None:
    with pytest.raises(ValueError, match="username and password"):
        SapPmConnector("cmms", SapPmConfig(type="sap-pm", base_url="https://s4.test"))


# --------------------------------------------------------------------------- both


@pytest.mark.parametrize("make", [lambda: maximo(FakeMaximo()), lambda: sap(FakeSap())])
def test_bad_write_backs_are_permanent(make: Any) -> None:
    connector = make()
    with pytest.raises(PermanentError, match="not supported"):
        connector.deliver("close_work_order", WORK_ORDER, KEY)
    with pytest.raises(PermanentError, match="plain identifier"):
        connector.deliver("create_work_order", {**WORK_ORDER, "external_id": 'WO" or 1=1'}, KEY)
    with pytest.raises(PermanentError, match="title"):
        connector.deliver("create_work_order", {**WORK_ORDER, "title": " "}, KEY)
    with pytest.raises(PermanentError, match="no mapping"):
        connector.deliver("create_work_order", {**WORK_ORDER, "priority": "URGENT"}, KEY)


def test_adapters_are_configured_from_one_json_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "S2_CONNECTORS",
        json.dumps(
            {
                "cmms": {"type": "maximo", "base_url": "https://m.test", "api_key": "k", "site_id": "HYD"},
                "erp": {"type": "sap-pm", "base_url": "https://s.test", "username": "u", "password": "p"},
                "samanvaya-fleet": {"type": "log"},
            }
        ),
    )
    built = build_connectors(ExecutorSettings().connectors)
    assert isinstance(built["cmms"], MaximoConnector)
    assert isinstance(built["erp"], SapPmConnector)


def test_an_adapter_missing_required_settings_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "S2_CONNECTORS", json.dumps({"cmms": {"type": "maximo", "base_url": "https://m.test"}})
    )
    with pytest.raises(ValueError, match="api_key"):
        ExecutorSettings()
