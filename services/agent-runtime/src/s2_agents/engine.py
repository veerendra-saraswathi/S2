"""The ontology engine's API, as the agent. Nothing else can change anything."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx

from s2_agents.settings import AgentSettings


class EngineError(Exception):
    def __init__(self, status: int, message: str, reasons: list[str] | None = None) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message
        self.reasons = reasons or []


class ClientCredentials:
    """Tokens for the agent's service account, reused until shortly before they expire."""

    def __init__(self, settings: AgentSettings, http: httpx.Client | None = None) -> None:
        self._settings = settings
        self._http = http or httpx.Client(timeout=10)
        self._token: str | None = None
        self._expires = 0.0

    def __call__(self) -> str:
        if self._token is None or time.monotonic() > self._expires:
            s = self._settings
            response = self._http.post(
                s.oidc_token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": s.agent_client_id,
                    "client_secret": s.agent_client_secret,
                },
            )
            if response.status_code != 200:
                hint = (
                    " (on the local stack: make dev-agent-client)"
                    if response.status_code in (400, 401)
                    else ""
                )
                raise EngineError(response.status_code, f"could not get a token: {response.text[:200]}{hint}")
            body = response.json()
            self._token = body["access_token"]
            self._expires = time.monotonic() + max(float(body.get("expires_in", 60)) - 30, 5)
        return self._token


class Engine:
    def __init__(self, http: httpx.Client, token: Callable[[], str]) -> None:
        self._http = http
        self._token = token

    @classmethod
    def from_settings(cls, settings: AgentSettings) -> Engine:
        return cls(httpx.Client(base_url=settings.engine_api_url, timeout=30), ClientCredentials(settings))

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {"Authorization": f"Bearer {self._token()}", **kwargs.pop("headers", {})}
        response = self._http.request(method, path, headers=headers, **kwargs)
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = response.text[:200]
            if isinstance(detail, dict):
                raise EngineError(
                    response.status_code, str(detail.get("message")), list(detail.get("reasons", []))
                )
            raise EngineError(response.status_code, str(detail))
        return response.json() if response.content else None

    def triage_queues(self) -> list[dict[str, Any]]:
        return self._call("GET", "/v1/triage/queues")

    def triage_queue(self, rule: str) -> dict[str, Any]:
        return self._call("GET", f"/v1/triage/queues/{rule}")

    def action_types(self) -> list[dict[str, Any]]:
        return self._call("GET", "/v1/ontology/action-types")

    def object_type(self, name: str) -> dict[str, Any]:
        return self._call("GET", f"/v1/ontology/object-types/{name}")

    def object(self, object_type: str, key: str) -> dict[str, Any]:
        return self._call("GET", f"/v1/objects/{object_type}/{key}")

    def timeseries(
        self, object_type: str, key: str, prop: str, start: str, limit: int = 1000
    ) -> dict[str, Any]:
        return self._call(
            "GET",
            f"/v1/objects/{object_type}/{key}/timeseries/{prop}",
            params={"start": start, "limit": limit},
        )

    def executions(self, *, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit, **({"status": status} if status else {})}
        return self._call("GET", "/v1/action-executions", params=params)

    def propose(self, action: str, parameters: dict[str, Any], rationale: str) -> dict[str, Any]:
        return self._call(
            "POST",
            f"/v1/actions/{action}/apply",
            json={"parameters": parameters, "rationale": rationale},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
