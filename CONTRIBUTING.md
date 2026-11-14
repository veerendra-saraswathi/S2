# Contributing to S2

## Set up

```bash
make sync        # Python workspace into .venv (needs uv)
pnpm install     # only if you work on the UI
make up          # the local stack (needs Docker)
```

## Before you open a pull request

```bash
make fmt         # format and apply safe lint fixes
make lint
make test-stack  # the full suite against the running stack
make policy-test # if you touched policies/
```

## Conventions

- **The ontology engine is the only writer of ontology state.** Other services call its API.
- **Schema changes are Alembic revisions.** Never edit a revision that has been merged. New tables get `tenant_id` and the `tenant_isolation` row-level security policy.
- **Ontology changes are pack changes** (`ontologies/`). Follow the rules in `ontologies/README.md`; breaking changes are refused by the engine.
- **Every new action gets a policy and policy tests.** Rules must deny when input is missing.
- **Anything that changes state is audited**, in the same transaction as the change.
- **Decisions with consequences get a record** in `docs/architecture/adr/`.
- Tests that touch the database use the `auth` and `tenant` fixtures so each test runs in its own tenant.

## Commit messages

Imperative mood, a short subject line, and a body that says why when the diff does not.
