"""OIDC access-token verification and the Principal every S2 service reasons about.

Claims read from the token (the dev realm in deploy/docker-compose/keycloak sets them):

    sub                  subject; recorded as the actor in the audit log
    tenant_id            UUID of the tenant the token is scoped to (required)
    realm_access.roles   Keycloak realm roles
    s2_actor_type        optional: "user" (default), "service" or "agent"
    s2_on_behalf_of      required for agents: the human the agent acts for, by subject or
                         username (a Keycloak hardcoded claim can only hold a constant,
                         and subjects differ between realms, so dev agents use the username)
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import jwt

# Asymmetric algorithms only. A token signed with a shared secret, or not signed at
# all, is rejected whatever its claims say.
ALGORITHMS = ("RS256", "ES256")
ACTOR_TYPES = frozenset({"user", "service", "agent"})


class AuthError(Exception):
    """The bearer token is malformed, expired, wrongly signed or not meant for this service."""


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    tenant_id: uuid.UUID
    roles: frozenset[str]
    actor_type: str = "user"
    on_behalf_of: str | None = None
    username: str | None = None

    def has_any_role(self, *roles: str) -> bool:
        return any(role in self.roles for role in roles)


KeyResolver = Callable[[str], Any]


class TokenVerifier:
    """Validates signature, issuer, audience and expiry, then maps claims to a Principal.

    `issuer` is the URL tokens are minted for (what a browser sees). Signing keys come
    from `jwks_url`, which inside a cluster is usually a different host, or from
    `key_resolver` (tests).
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str | None = None,
        key_resolver: KeyResolver | None = None,
        leeway_s: int = 30,
    ) -> None:
        if key_resolver is None:
            if not jwks_url:
                raise ValueError("either jwks_url or key_resolver is required")
            jwks = jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=300, timeout=5)

            def key_resolver(token: str) -> Any:
                return jwks.get_signing_key_from_jwt(token).key

        self._issuer = issuer
        self._audience = audience
        self._resolve_key = key_resolver
        self._leeway_s = leeway_s

    def verify(self, token: str) -> Principal:
        try:
            claims = jwt.decode(
                token,
                self._resolve_key(token),
                algorithms=list(ALGORITHMS),
                issuer=self._issuer,
                audience=self._audience,
                leeway=self._leeway_s,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthError(f"invalid token: {exc}") from exc
        return principal_from_claims(claims)


def principal_from_claims(claims: dict[str, Any]) -> Principal:
    raw_tenant = claims.get("tenant_id")
    if not raw_tenant:
        raise AuthError("the token carries no tenant_id claim")
    try:
        tenant_id = uuid.UUID(str(raw_tenant))
    except ValueError as exc:
        raise AuthError("the tenant_id claim is not a UUID") from exc

    actor_type = claims.get("s2_actor_type", "user")
    if actor_type not in ACTOR_TYPES:
        raise AuthError(f"unknown s2_actor_type {actor_type!r}")
    on_behalf_of = claims.get("s2_on_behalf_of")
    if actor_type == "agent" and not on_behalf_of:
        raise AuthError("an agent token must name the human it acts for (s2_on_behalf_of)")

    roles = (claims.get("realm_access") or {}).get("roles") or []
    return Principal(
        subject=str(claims["sub"]),
        tenant_id=tenant_id,
        roles=frozenset(str(role) for role in roles),
        actor_type=actor_type,
        on_behalf_of=str(on_behalf_of) if on_behalf_of else None,
        username=claims.get("preferred_username"),
    )
