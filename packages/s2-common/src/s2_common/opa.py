"""Client for Open Policy Agent's data API.

Policy contract: the document at `policy_path` exposes

    allow   boolean
    deny    set of human-readable reasons (optional)

The decision is `allow is true AND deny is empty`. Anything else, including an
undefined document or a malformed response, is a denial: the client fails closed.
An unreachable OPA raises PolicyError so the caller can report 503 rather than guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

POLICY_PATH_PATTERN = r"^[a-z][a-z0-9_]*(/[a-z][a-z0-9_]*)*$"
_POLICY_PATH = re.compile(POLICY_PATH_PATTERN)


class PolicyError(Exception):
    """OPA could not be consulted."""


@dataclass(frozen=True, slots=True)
class Decision:
    allow: bool
    reasons: tuple[str, ...] = ()
    decision_id: str | None = None


class OpaClient:
    def __init__(self, base_url: str, *, timeout_s: float = 2.0, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_s)

    def decide(self, policy_path: str, input_doc: dict[str, Any]) -> Decision:
        if not _POLICY_PATH.fullmatch(policy_path):
            raise PolicyError(f"invalid policy path: {policy_path!r}")
        try:
            response = self._client.post(f"/v1/data/{policy_path}", json={"input": input_doc})
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PolicyError(f"policy engine unavailable: {exc}") from exc

        decision_id = body.get("decision_id") if isinstance(body, dict) else None
        result = body.get("result") if isinstance(body, dict) else None
        if not isinstance(result, dict):
            return Decision(False, ("policy returned no decision",), decision_id)

        deny = result.get("deny") or []
        reasons = (
            tuple(sorted(str(reason) for reason in deny))
            if isinstance(deny, list)
            else ("malformed deny set",)
        )
        allowed = result.get("allow") is True and not reasons
        if not allowed and not reasons:
            reasons = ("not allowed by policy",)
        return Decision(allowed, reasons, decision_id)

    def healthy(self) -> bool:
        try:
            return self._client.get("/health").status_code == 200
        except httpx.HTTPError:
            return False

    def close(self) -> None:
        self._client.close()
