"""Application factory. Tests build the app with their own verifier and policy client."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from s2_common.auth import TokenVerifier
from s2_common.logging import configure_logging
from s2_common.opa import OpaClient
from s2_common.settings import Settings, get_settings
from s2_ontology import __version__
from s2_ontology.api import actions, audit, health, objects, ontology, triage
from s2_ontology.db.session import make_engine, make_session_factory
from s2_ontology.domain.errors import DomainError

log = logging.getLogger("s2.ontology")


def assert_row_level_security_applies(engine: Engine) -> None:
    """Refuse to start if the database role would bypass tenant isolation.

    Superusers and BYPASSRLS roles skip row-level security silently, which would turn a
    connection-string mistake into every tenant seeing every other tenant's data.
    """
    with engine.connect() as connection:
        bypasses = connection.execute(
            text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).scalar()
    if bypasses:
        raise RuntimeError(
            "S2_DATABASE_URL connects as a role that bypasses row-level security. "
            "Use the application role (s2_app); the schema owner is only for migrations."
        )


def create_app(
    settings: Settings | None = None,
    *,
    verifier: TokenVerifier | None = None,
    opa: OpaClient | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    engine = make_engine(settings.database_url)
    telemetry_engine = (
        make_engine(settings.telemetry_database_url) if settings.telemetry_database_url else None
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        assert_row_level_security_applies(engine)
        yield
        app.state.opa.close()
        engine.dispose()
        if telemetry_engine is not None:
            telemetry_engine.dispose()

    app = FastAPI(
        title="S2 Ontology Engine",
        version=__version__,
        summary="Object types, links, governed actions and the audit chain of the Samanvaya 2 digital twin.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.telemetry_engine = telemetry_engine
    app.state.verifier = verifier or TokenVerifier(
        issuer=settings.oidc_issuer, audience=settings.oidc_audience, jwks_url=settings.oidc_jwks_url
    )
    app.state.opa = opa or OpaClient(settings.opa_url, timeout_s=settings.opa_timeout_s)

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content={"detail": {"message": exc.message, **exc.extra}}
        )

    @app.exception_handler(IntegrityError)
    @app.exception_handler(StaleDataError)
    async def lost_race(_: Request, exc: Exception) -> JSONResponse:
        # Two requests raced for the same row (same key, same Idempotency-Key, same
        # version). The loser's transaction is rolled back; the caller may retry.
        log.info("request lost a race: %s", type(exc).__name__)
        message = "the request conflicts with a concurrent change; nothing was applied, retry"
        return JSONResponse(status_code=409, content={"detail": {"message": message}})

    for module in (health, ontology, objects, actions, audit, triage):
        app.include_router(module.router)
    return app
