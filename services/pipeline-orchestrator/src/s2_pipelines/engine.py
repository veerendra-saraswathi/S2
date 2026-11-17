"""A small client for the ontology engine's API, as the pipeline's identity."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx

from s2_pipelines.settings import PipelineSettings

PAGE = 1000


class EngineError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


class TokenSource:
    """Client credentials when a secret is configured, the dev password grant otherwise.
    Tokens are reused until shortly before they expire."""

    def __init__(self, settings: PipelineSettings, http: httpx.Client | None = None) -> None:
        self._settings = settings
        self._http = http or httpx.Client(timeout=10)
        self._token: str | None = None
        self._expires = 0.0

    def __call__(self) -> str:
        if self._token is None or time.monotonic() > self._expires:
            s = self._settings
            if s.pipeline_client_id and s.pipeline_client_secret:
                data = {
                    "grant_type": "client_credentials",
                    "client_id": s.pipeline_client_id,
                    "client_secret": s.pipeline_client_secret,
                }
            else:
                data = {
                    "grant_type": "password",
                    "client_id": s.oidc_cli_client_id,
                    "username": s.pipeline_user,
                    "password": s.pipeline_password,
                }
            response = self._http.post(s.oidc_token_url, data=data)
            if response.status_code != 200:
                raise EngineError(response.status_code, f"could not get a token: {response.text[:300]}")
            body = response.json()
            self._token = body["access_token"]
            self._expires = time.monotonic() + max(float(body.get("expires_in", 60)) - 30, 5)
        return self._token


def _message(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        return response.text[:300]
    if isinstance(detail, dict):
        return str(detail.get("message", detail))
    return str(detail)


class EngineClient:
    def __init__(self, http: httpx.Client, token: Callable[[], str]) -> None:
        self._http = http
        self._token = token

    @classmethod
    def from_settings(cls, settings: PipelineSettings) -> EngineClient:
        return cls(httpx.Client(base_url=settings.engine_api_url, timeout=30), TokenSource(settings))

    def _call(
        self, method: str, path: str, headers: dict[str, str] | None = None, **kwargs: Any
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._token()}", **(headers or {})}
        return self._http.request(method, path, headers=headers, **kwargs)

    def _ok(self, response: httpx.Response) -> Any:
        if response.status_code >= 400:
            raise EngineError(response.status_code, _message(response))
        return response.json() if response.content else None

    def object_type(self, name: str) -> dict[str, Any]:
        return self._ok(self._call("GET", f"/v1/ontology/object-types/{name}"))

    def objects(self, object_type: str) -> Iterator[dict[str, Any]]:
        after: str | None = None
        while True:
            params: dict[str, Any] = {"limit": PAGE, **({"after": after} if after else {})}
            page = self._ok(self._call("GET", f"/v1/objects/{object_type}", params=params))
            yield from page
            if len(page) < PAGE:
                return
            after = page[-1]["primary_key"]

    def create(
        self, object_type: str, key: str, properties: dict[str, Any], provenance: dict[str, Any]
    ) -> None:
        """Create only: 409 if the object already exists (it never replaces one)."""
        body = {"properties": properties, "provenance": provenance}
        path = f"/v1/objects/{object_type}/{key}"
        self._ok(self._call("PUT", path, headers={"If-None-Match": "*"}, json=body))

    def patch(
        self, object_type: str, key: str, properties: dict[str, Any], provenance: dict[str, Any]
    ) -> None:
        body = {"properties": properties, "provenance": provenance}
        self._ok(self._call("PATCH", f"/v1/objects/{object_type}/{key}", json=body))
