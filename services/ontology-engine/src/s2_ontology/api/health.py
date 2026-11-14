"""Liveness, readiness and "who am I"."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from s2_common.auth import Principal
from s2_ontology.api.deps import get_principal
from s2_ontology.api.schemas import Me

router = APIRouter(tags=["health"])


@router.get("/healthz", summary="Liveness: the process is up")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", summary="Readiness: the database and the policy engine are reachable")
def readyz(request: Request, response: Response) -> dict[str, object]:
    database = False
    try:
        with request.app.state.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        database = True
    except SQLAlchemyError:
        pass
    checks = {"database": database, "policy_engine": request.app.state.opa.healthy()}
    ready = all(checks.values())
    response.status_code = 200 if ready else 503
    return {"status": "ready" if ready else "not_ready", "checks": checks}


@router.get("/v1/me", response_model=Me, summary="The identity this service derived from your token")
def me(principal: Principal = Depends(get_principal)) -> Me:
    return Me(
        subject=principal.subject,
        username=principal.username,
        tenant_id=principal.tenant_id,
        roles=sorted(principal.roles),
        actor_type=principal.actor_type,
        on_behalf_of=principal.on_behalf_of,
    )
