"""Request-scoped dependencies: who is calling, their tenant-scoped session, role checks.

Two kinds of authorisation live in this service. Plain API operations (reading the
ontology, administering it, reading the audit log) are role checks, here. Actions,
where authorisation depends on the state of the twin, are decided by OPA
(domain/actions.py).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from s2_common.auth import AuthError, Principal
from s2_common.opa import OpaClient
from s2_ontology.db.session import tenant_session
from s2_ontology.domain.errors import Denied, Unauthenticated

ONTOLOGY_ADMIN = "ontology-admin"
APPROVER = "approver"
AUDITOR = "auditor"

_bearer = HTTPBearer(auto_error=False, description="An access token issued by the S2 realm")


def get_principal(
    request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)
) -> Principal:
    if credentials is None:
        raise Unauthenticated("a bearer token is required")
    try:
        return request.app.state.verifier.verify(credentials.credentials)
    except AuthError as exc:
        raise Unauthenticated(str(exc)) from exc


def get_session(request: Request, principal: Principal = Depends(get_principal)) -> Iterator[Session]:
    """A session bound to the caller's tenant. Handlers commit explicitly; anything left
    uncommitted when the request ends is rolled back."""
    session = tenant_session(request.app.state.session_factory, principal.tenant_id)
    try:
        yield session
    finally:
        session.close()


def get_opa(request: Request) -> OpaClient:
    return request.app.state.opa


def require_roles(*roles: str) -> Callable[..., Principal]:
    """The caller must hold at least one of `roles`."""

    def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.has_any_role(*roles):
            raise Denied(
                f"this operation needs one of the roles: {', '.join(roles)}", required_roles=list(roles)
            )
        return principal

    return dependency
