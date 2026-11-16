"""Connectors, with no network: httpx's mock transport stands in for the target."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from s2_executor.connectors import (
    HttpConnector,
    LogConnector,
    PermanentError,
    TransientError,
    build_connectors,
)
from s2_executor.settings import ConnectorConfig, ExecutorSettings

KEY = uuid.uuid4()


def make(handler, **config) -> HttpConnector:
    cfg = ConnectorConfig(type="http", base_url="https://cmms.test/api", **config)
    client = httpx.Client(base_url=cfg.base_url, transport=httpx.MockTransport(handler))
    return HttpConnector("cmms", cfg, client=client)


def test_posts_json_with_the_idempotency_key():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers["Idempotency-Key"]
        seen["operation"] = request.headers["X-S2-Operation"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(201)

    make(handler).deliver("create_work_order", {"title": "x"}, KEY)

    assert seen == {
        "url": "https://cmms.test/api/create_work_order",
        "key": str(KEY),
        "operation": "create_work_order",
        "body": {"title": "x"},
    }


def test_operation_paths_can_be_mapped():
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200)

    connector = make(handler, paths={"create_work_order": "/v2/work-orders"})
    connector.deliver("create_work_order", {}, KEY)
    connector.deliver("close_work_order", {}, KEY)

    assert urls == ["https://cmms.test/api/v2/work-orders", "https://cmms.test/api/close_work_order"]


@pytest.mark.parametrize("status", [500, 502, 503, 408, 425, 429])
def test_server_errors_and_not_now_are_transient(status: int):
    with pytest.raises(TransientError, match=str(status)):
        make(lambda _: httpx.Response(status, text="try later")).deliver("op", {}, KEY)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 301])
def test_other_refusals_are_permanent(status: int):
    with pytest.raises(PermanentError, match=str(status)):
        make(lambda _: httpx.Response(status, text="no")).deliver("op", {}, KEY)


def test_timeouts_and_connection_failures_are_transient():
    def timeout(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    def refused(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(TransientError, match="timed out"):
        make(timeout).deliver("op", {}, KEY)
    with pytest.raises(TransientError, match="ConnectError"):
        make(refused).deliver("op", {}, KEY)


def test_error_text_is_bounded_and_carries_no_payload():
    secret_payload = {"title": "board-level secret"}
    with pytest.raises(PermanentError) as caught:
        make(lambda _: httpx.Response(400, text="x" * 5000)).deliver("op", secret_payload, KEY)
    assert len(str(caught.value)) < 450
    assert "board-level secret" not in str(caught.value)


def test_http_connector_needs_a_base_url():
    with pytest.raises(ValueError, match="base_url"):
        HttpConnector("cmms", ConnectorConfig(type="http"))


def test_log_connector_never_fails():
    LogConnector("cmms").deliver("op", {"a": 1}, KEY)


def test_default_settings_deliver_nowhere():
    built = build_connectors(ExecutorSettings().connectors)
    assert set(built) == {"samanvaya-fleet", "cmms"}
    assert all(isinstance(c, LogConnector) for c in built.values())


def test_connectors_come_from_one_json_variable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(
        "S2_CONNECTORS",
        json.dumps({"cmms": {"type": "http", "base_url": "https://cmms.test/api/", "timeout_s": 3}}),
    )
    settings = ExecutorSettings()
    assert settings.connectors["cmms"].base_url == "https://cmms.test/api"
    assert settings.connectors["cmms"].timeout_s == 3
    assert isinstance(build_connectors(settings.connectors)["cmms"], HttpConnector)


def test_unknown_connector_settings_are_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("S2_CONNECTORS", json.dumps({"cmms": {"type": "http", "base_ul": "typo"}}))
    with pytest.raises(ValueError):
        ExecutorSettings()
