"""The audit log: read it, and re-verify its hash chain."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from s2_common.auth import Principal
from s2_ontology.api.deps import AUDITOR, ONTOLOGY_ADMIN, get_session, require_roles
from s2_ontology.api.schemas import AuditEntryOut, AuditVerification
from s2_ontology.domain import audit

router = APIRouter(prefix="/v1/audit", tags=["audit"])

_readers = require_roles(AUDITOR, ONTOLOGY_ADMIN)


@router.get("", response_model=list[AuditEntryOut], summary="Audit entries, newest first")
def list_audit(
    event_type: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    before_seq: Annotated[
        int | None, Query(ge=1, description="Page backwards: entries older than this")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    _: Principal = Depends(_readers),
    session: Session = Depends(get_session),
) -> list[AuditEntryOut]:
    entries = audit.list_entries(
        session,
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        before_seq=before_seq,
        limit=limit,
    )
    return [AuditEntryOut.model_validate(entry) for entry in entries]


@router.get(
    "/verify",
    response_model=AuditVerification,
    summary="Re-walk this tenant's hash chain",
    description="`entries` is how many were checked; `first_bad_seq` is where the chain breaks, if it does.",
)
def verify_audit(
    principal: Principal = Depends(_readers), session: Session = Depends(get_session)
) -> AuditVerification:
    entries, first_bad_seq = audit.verify(session, principal.tenant_id)
    return AuditVerification(entries=entries, verified=first_bad_seq is None, first_bad_seq=first_bad_seq)
