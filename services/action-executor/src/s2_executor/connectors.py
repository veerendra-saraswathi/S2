"""Connectors: the part of the executor that talks to the outside world.

A connector delivers one write-back and either returns or raises. Two failure kinds
matter to the relay and nothing else does:

* `TransientError`: try again later (timeout, connection refused, 5xx, 429).
* `PermanentError`: the target understood and refused (4xx, unknown operation). Retrying
  the same payload cannot help, so the write-back goes straight to dead letter.

Delivery is at-least-once: the same write-back can arrive twice after a crash or a lost
response. Every call carries the outbox row id as `Idempotency-Key`, and a target must
treat a repeat as the same request.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Protocol

import httpx

from s2_executor.settings import ConnectorConfig

log = logging.getLogger("s2.executor.connector")

# 408 and 429 are the client errors that mean "not now", not "no".
_RETRYABLE_4XX = frozenset({408, 425, 429})
_ERROR_BODY_CHARS = 300


class DeliveryError(Exception):
    """Base class. The message is stored in outbox.last_error, so keep it short and free
    of secrets and payload data."""


class TransientError(DeliveryError):
    pass


class PermanentError(DeliveryError):
    pass


class Connector(Protocol):
    def deliver(self, operation: str, payload: dict[str, Any], idempotency_key: uuid.UUID) -> None: ...

    def close(self) -> None: ...


class LogConnector:
    """Records the write-back in the log and succeeds. For the local stack and demos."""

    def __init__(self, name: str) -> None:
        self._name = name

    def deliver(self, operation: str, payload: dict[str, Any], idempotency_key: uuid.UUID) -> None:
        log.info(
            "write-back recorded, not sent",
            extra={
                "connector": self._name,
                "operation": operation,
                "idempotency_key": str(idempotency_key),
                "payload_keys": sorted(payload),
            },
        )

    def close(self) -> None:
        return None


class HttpConnector:
    """POSTs the payload as JSON to `<base_url>/<path>`."""

    def __init__(self, name: str, config: ConnectorConfig, *, client: httpx.Client | None = None) -> None:
        if not config.base_url:
            raise ValueError(f"connector {name!r}: type http needs base_url")
        self._name = name
        self._paths = config.paths
        headers = {"User-Agent": "s2-action-executor"}
        if config.bearer_token:
            headers["Authorization"] = f"Bearer {config.bearer_token}"
        self._client = client or httpx.Client(
            base_url=config.base_url, timeout=config.timeout_s, headers=headers
        )

    def deliver(self, operation: str, payload: dict[str, Any], idempotency_key: uuid.UUID) -> None:
        path = self._paths.get(operation, f"/{operation}")
        try:
            response = self._client.post(
                path,
                json=payload,
                headers={"Idempotency-Key": str(idempotency_key), "X-S2-Operation": operation},
            )
        except httpx.TimeoutException as exc:
            raise TransientError(f"{self._name}: timed out ({type(exc).__name__})") from exc
        except httpx.TransportError as exc:
            raise TransientError(f"{self._name}: {type(exc).__name__}: {exc}") from exc

        status = response.status_code
        if status < 300:
            return
        detail = f"{self._name}: HTTP {status} for {operation}: {response.text[:_ERROR_BODY_CHARS]!r}"
        if status >= 500 or status in _RETRYABLE_4XX:
            raise TransientError(detail)
        # Other 3xx, and 4xx: the target has made up its mind.
        raise PermanentError(detail)

    def close(self) -> None:
        self._client.close()


def build_connectors(configs: dict[str, ConnectorConfig]) -> dict[str, Connector]:
    built: dict[str, Connector] = {}
    for name, config in configs.items():
        built[name] = HttpConnector(name, config) if config.type == "http" else LogConnector(name)
    return built
