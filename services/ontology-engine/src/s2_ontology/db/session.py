"""Engine and tenant-scoped sessions.

Tenant isolation is enforced by Postgres row-level security keyed on the transaction-local
setting `s2.tenant_id`. A session created by `tenant_session` re-applies that setting at
the start of every transaction it opens, so application code never sets it by hand and a
pooled connection never carries one request's tenant into the next.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

_TENANT_KEY = "s2_tenant_id"


def make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=10)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def tenant_session(factory: sessionmaker[Session], tenant_id: uuid.UUID) -> Session:
    session = factory()
    session.info[_TENANT_KEY] = str(tenant_id)
    return session


@event.listens_for(Session, "after_begin")
def _apply_tenant(session: Session, _transaction, connection) -> None:
    tenant_id = session.info.get(_TENANT_KEY)
    if tenant_id:
        connection.execute(text("SELECT set_config('s2.tenant_id', :tenant, true)"), {"tenant": tenant_id})
