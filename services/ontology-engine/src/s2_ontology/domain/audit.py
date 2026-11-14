"""Writing to, reading and verifying the append-only audit chain.

The chain itself (sequence numbers, hashes, immutability) is enforced by database
triggers; see migrations/versions/0001_ontology_kernel.py. This module only fills in
who did what.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from s2_common.auth import Principal
from s2_ontology.db.models import ActorType, AuditLog, AuditOutcome


def record(
    session: Session,
    principal: Principal,
    event_type: str,
    *,
    outcome: AuditOutcome = AuditOutcome.SUCCESS,
    resource_type: str | None = None,
    resource_id: str | None = None,
    details: dict[str, Any] | None = None,
    policy_decision_id: str | None = None,
) -> AuditLog:
    """Append one entry inside the caller's transaction.

    The insert takes a per-tenant lock that is held until the transaction ends, so call
    this last, just before committing, to keep that window short.
    """
    entry = AuditLog(
        tenant_id=principal.tenant_id,
        actor_type=ActorType(principal.actor_type),
        actor_id=principal.subject,
        on_behalf_of=principal.on_behalf_of,
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        policy_decision_id=policy_decision_id,
        details=details or {},
    )
    session.add(entry)
    session.flush()
    return entry


def list_entries(
    session: Session,
    *,
    event_type: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    before_seq: int | None = None,
    limit: int = 100,
) -> list[AuditLog]:
    """Newest first. Page backwards with `before_seq`."""
    query = select(AuditLog)
    if event_type is not None:
        query = query.where(AuditLog.event_type == event_type)
    if resource_type is not None:
        query = query.where(AuditLog.resource_type == resource_type)
    if resource_id is not None:
        query = query.where(AuditLog.resource_id == resource_id)
    if before_seq is not None:
        query = query.where(AuditLog.seq < before_seq)
    return list(session.scalars(query.order_by(AuditLog.seq.desc()).limit(limit)))


def verify(session: Session, tenant_id: uuid.UUID) -> tuple[int, int | None]:
    """Re-walk the tenant's hash chain. Returns (entries checked, first bad seq or None)."""
    row = session.execute(
        text("SELECT entries, first_bad_seq FROM ontology.audit_verify(:tenant)"), {"tenant": tenant_id}
    ).one()
    return row.entries, row.first_bad_seq
