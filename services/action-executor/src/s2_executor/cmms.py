"""Work-order adapters for real maintenance systems: IBM Maximo and SAP PM.

Both turn the ontology's generic `create_work_order` write-back

    {"external_id": "WO-123", "title": "...", "priority": "HIGH", "description": "..."}

into the system's own record, and both make a retried delivery safe the same way: look
the record up by S2's work-order id first, and create it only if it is not there. That
matters because delivery is at-least-once (ADR 0009): after a timeout the executor
cannot tell whether the first attempt landed.

Any other operation is a permanent error: the adapter does not guess what it means.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import httpx

from s2_executor.connectors import PermanentError, TransientError, check, send
from s2_executor.settings import MaximoConfig, SapPmConfig

CREATE_WORK_ORDER = "create_work_order"
# Work-order ids go into query filters, so only plain identifiers are accepted.
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:/-]{1,64}$")

MAXIMO_DESCRIPTION_MAX = 100  # WORKORDER.DESCRIPTION
SAP_TEXT_MAX = 40  # QMTXT, the notification's short text


def _work_order(
    name: str, operation: str, payload: dict[str, Any]
) -> tuple[str, str, str | None, str | None]:
    if operation != CREATE_WORK_ORDER:
        raise PermanentError(f"{name}: operation {operation!r} is not supported (only {CREATE_WORK_ORDER})")
    external_id = str(payload.get("external_id") or "")
    if not _SAFE_ID.match(external_id):
        raise PermanentError(f"{name}: external_id {external_id!r} is missing or not a plain identifier")
    title = str(payload.get("title") or "").strip()
    if not title:
        raise PermanentError(f"{name}: a work order needs a title")
    priority = payload.get("priority")
    description = payload.get("description")
    return external_id, title, str(priority) if priority else None, str(description) if description else None


def _priority(name: str, priorities: dict[str, str], priority: str | None) -> str | None:
    if priority is None:
        return None
    if priority not in priorities:
        raise PermanentError(f"{name}: priority {priority!r} has no mapping; configure `priorities`")
    return priorities[priority]


# --------------------------------------------------------------------------- Maximo


class MaximoConnector:
    """POST /maximo/api/os/<object structure>?lean=1, authenticated with an API key."""

    def __init__(self, name: str, config: MaximoConfig, *, client: httpx.Client | None = None) -> None:
        self._name = name
        self._config = config
        self._path = f"/maximo/api/os/{config.object_structure}"
        self._client = client or httpx.Client(
            base_url=config.base_url,
            timeout=config.timeout_s,
            headers={
                "apikey": config.api_key,
                "Accept": "application/json",
                "User-Agent": "s2-action-executor",
            },
        )

    def find(self, external_id: str) -> str | None:
        """The wonum of the work order carrying this S2 id, if one exists."""
        params = {
            "lean": "1",
            "oslc.select": "wonum",
            "oslc.where": f'{self._config.reference_field}="{external_id}" '
            f'and siteid="{self._config.site_id}"',
        }
        response = check(
            send(self._client, self._name, "GET", self._path, params=params), self._name, "lookup"
        )
        members = response.json().get("member", [])
        return str(members[0].get("wonum")) if members else None

    def deliver(self, operation: str, payload: dict[str, Any], idempotency_key: uuid.UUID) -> None:
        external_id, title, priority, description = _work_order(self._name, operation, payload)
        if self.find(external_id) is not None:
            return  # an earlier attempt made it
        body: dict[str, Any] = {
            "siteid": self._config.site_id,
            "description": title[:MAXIMO_DESCRIPTION_MAX],
            self._config.reference_field: external_id,
        }
        mapped = _priority(self._name, self._config.priorities, priority)
        if mapped is not None:
            body["wopriority"] = int(mapped) if mapped.isdigit() else mapped
        if description or len(title) > MAXIMO_DESCRIPTION_MAX:
            body["description_longdescription"] = description or title
        if self._config.work_type:
            body["worktype"] = self._config.work_type
        response = send(
            self._client,
            self._name,
            "POST",
            self._path,
            params={"lean": "1"},
            headers={"properties": "wonum"},
            json=body,
        )
        check(response, self._name, operation)

    def close(self) -> None:
        self._client.close()


# --------------------------------------------------------------------------- SAP PM


class SapPmConnector:
    """A maintenance notification through API_MAINTNOTIFICATION (OData v2).

    SAP refuses changes without a CSRF token, which is fetched with a GET and is bound to
    the session cookies that came with it, so the client keeps both. A token that has
    expired is refetched once.
    """

    ENTITY = "/MaintenanceNotification"

    def __init__(self, name: str, config: SapPmConfig, *, client: httpx.Client | None = None) -> None:
        if not (config.bearer_token or (config.username and config.password)):
            raise ValueError(f"connector {name!r}: type sap-pm needs username and password, or bearer_token")
        self._name = name
        self._config = config
        headers = {"Accept": "application/json", "User-Agent": "s2-action-executor"}
        if config.bearer_token:
            headers["Authorization"] = f"Bearer {config.bearer_token}"
        auth = (config.username, config.password) if config.username and config.password else None
        self._client = client or httpx.Client(
            base_url=config.base_url + config.service_path,
            timeout=config.timeout_s,
            headers=headers,
            auth=auth,
        )
        if client is not None and auth is not None:
            self._client.auth = auth
        self._token: str | None = None

    def _params(self, **extra: str) -> dict[str, str]:
        return {**({"sap-client": self._config.sap_client} if self._config.sap_client else {}), **extra}

    def _fetch_token(self) -> str:
        response = send(
            self._client,
            self._name,
            "GET",
            self.ENTITY,
            params=self._params(**{"$top": "0"}),
            headers={"x-csrf-token": "Fetch"},
        )
        check(response, self._name, "CSRF token")
        token = response.headers.get("x-csrf-token")
        if not token:
            raise TransientError(f"{self._name}: the service returned no CSRF token")
        self._token = token
        return token

    def find(self, external_id: str) -> str | None:
        field = self._config.reference_field
        if not field:
            return None
        params = self._params(
            **{"$filter": f"{field} eq '{external_id}'", "$top": "1", "$select": "MaintenanceNotification"}
        )
        response = check(
            send(self._client, self._name, "GET", self.ENTITY, params=params), self._name, "lookup"
        )
        results = response.json().get("d", {}).get("results", [])
        return str(results[0].get("MaintenanceNotification")) if results else None

    def deliver(self, operation: str, payload: dict[str, Any], idempotency_key: uuid.UUID) -> None:
        external_id, title, priority, description = _work_order(self._name, operation, payload)
        if self.find(external_id) is not None:
            return
        body: dict[str, Any] = {
            "NotificationText": title[:SAP_TEXT_MAX],
            "NotificationType": self._config.notification_type,
        }
        mapped = _priority(self._name, self._config.priorities, priority)
        if mapped is not None:
            body["MaintPriority"] = mapped
        if self._config.reference_field:
            body[self._config.reference_field] = external_id
        del description  # long texts are a separate entity; the title is what a planner sees first

        for attempt in (1, 2):
            token = self._token or self._fetch_token()
            response = send(
                self._client,
                self._name,
                "POST",
                self.ENTITY,
                params=self._params(),
                headers={"x-csrf-token": token, "Content-Type": "application/json"},
                json=body,
            )
            if response.status_code == 403 and response.headers.get("x-csrf-token", "").lower() == "required":
                self._token = None  # expired with its session: fetch a new one and try once more
                if attempt == 1:
                    continue
            check(response, self._name, operation)
            return

    def close(self) -> None:
        self._client.close()
