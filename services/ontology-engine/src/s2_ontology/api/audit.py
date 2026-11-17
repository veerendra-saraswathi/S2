"""The audit log: read it, and re-verify its hash chain.

Entries are returned with the caller's markings applied: values of properties they are
not cleared for are taken out of `details`, and entries about object types they are not
cleared for keep only their shape (domain/markings.py). Such entries say `redacted`.
The chain itself is verified over the stored entries, so redaction never affects it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from s2_common.auth import Principal
from s2_ontology.api.deps import AUDITOR, ONTOLOGY_ADMIN, get_session, require_roles
from s2_ontology.api.schemas import AuditEntryOut, AuditVerification
from s2_ontology.domain import audit, markings, registry

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
    principal: Principal = Depends(_readers),
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
    redactor = markings.AuditRedactor(principal, lambda name: registry.find_object_type(session, name))
    out = []
    for entry in entries:
        item = AuditEntryOut.model_validate(entry)
        details, redacted = redactor.redact(item.details)
        update: dict[str, object] = {"details": details}
        if redactor.resource_hidden(item.resource_type):
            # The primary key of an object the caller cannot see is itself withheld.
            update["resource_id"] = None
            redacted = True
        out.append(item.model_copy(update={**update, "redacted": True}) if redacted else item)
    return out


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
