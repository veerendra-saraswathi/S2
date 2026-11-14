"""Alembic environment for the ontology schema."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from s2_common.settings import get_settings
from s2_ontology.db.models import Base

config = context.config
if config.config_file_name is not None:
    # Keep the application's loggers: tests and the CLI call Alembic in-process.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def _include_name(name: str | None, type_: str, _parent_names: dict[str, str | None]) -> bool:
    # Only the `ontology` schema is ours. PostGIS and Alembic keep tables in `public`.
    if type_ == "schema":
        return name == "ontology"
    return True


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            include_name=_include_name,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit("offline migrations are not supported; run against a database")
run_migrations_online()
