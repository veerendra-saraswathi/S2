-- Runs once, when the `postgres` container initialises an empty data directory,
-- connected to database `s2` as the superuser.
--
-- The application role is created here, with a password, before any migration runs.
-- The `migrate` service then creates the `ontology` schema and grants this role DML.

\set app_password `echo "$S2_APP_DB_PASSWORD"`
CREATE ROLE s2_app LOGIN PASSWORD :'app_password';

-- The action executor's role. Migration 0002 gives it BYPASSRLS (it serves every tenant)
-- and narrow rights on the outbox and audit log.
\set executor_password `echo "$S2_EXECUTOR_DB_PASSWORD"`
CREATE ROLE s2_executor LOGIN PASSWORD :'executor_password';

-- Keycloak keeps its own state in a separate database on the same server.
CREATE DATABASE keycloak;

CREATE EXTENSION IF NOT EXISTS postgis;
