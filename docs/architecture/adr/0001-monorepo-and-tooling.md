# 0001. One repository; uv, pnpm and ruff

Status: accepted (November 2026)

## Context

S2 is several services, a shared library, a web application, policies, ontology definitions and deployment files, built at first by one developer. Changes routinely cut across them: a new action touches a pack, a policy, the engine and its tests.

## Decision

Keep everything in one repository.

- Python: one [uv](https://docs.astral.sh/uv/) workspace with a single lock file. Each service is a workspace member with its own `pyproject.toml`; the shared runtime is `packages/s2-common`. `ruff` lints and formats.
- JavaScript: a pnpm workspace rooted at the repository, with applications under `apps/`.
- Each service image is built from the repository root and installs only its own package (`uv sync --package`).
- Services planned for later phases exist as directories with a README stating their contract, not as empty code.

## Consequences

- One pull request can change a contract and everything that depends on it, and CI tests them together.
- All Python services share dependency versions. That is the point now; if a service ever needs to diverge it leaves the workspace.
- Build contexts are the whole repository, so `.dockerignore` matters.
