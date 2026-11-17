# S2 developer entry points. `make help` lists them.

COMPOSE := docker compose -f deploy/docker-compose/docker-compose.yml
S2CTL   := $(COMPOSE) exec -T ontology-engine python -m s2_ontology.cli
PACKS   := /app/ontologies/core-fleet/pack.yaml /app/ontologies/energy-grid/pack.yaml

# Used by `make test-stack`: the running Compose stack doubles as the test backend.
STACK_TEST_ENV := S2_TEST_ADMIN_URL=postgresql+psycopg://postgres:postgres_dev@localhost:5432/postgres \
                  S2_TEST_OPA_URL=http://localhost:8181

.DEFAULT_GOAL := help
.PHONY: help up down nuke ps logs seed sim sim-stop token psql sync lint fmt test test-stack policy-test ui

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  %-14s %s\n", $$1, $$2}'

# ---------------------------------------------------------------- local stack

up: ## Build and start the stack, then wait for the API to be ready
	$(COMPOSE) up -d --build
	@echo "waiting for the ontology engine (first start pulls images and can take a few minutes)..."
	@for i in $$(seq 1 150); do \
		if curl -fsS http://localhost:8000/readyz >/dev/null 2>&1; then \
			echo "ready: http://localhost:8000/docs   Workshop: http://localhost:5173"; exit 0; \
		fi; \
		sleep 2; \
	done; \
	echo "the API is not ready after 5 minutes. Run 'make ps' and 'make logs'."; exit 1

down: ## Stop the stack (data is kept)
	$(COMPOSE) --profile sim --profile pipelines down

nuke: ## Stop the stack and delete all local data
	$(COMPOSE) --profile sim --profile pipelines down -v

ps: ## Show service status
	$(COMPOSE) --profile sim ps

logs: ## Follow the logs of the S2 services
	$(COMPOSE) --profile sim logs -f --tail=100 migrate ontology-engine mqtt-bridge timeseries-sink action-executor

seed: ## Apply the ontology packs and create demo objects
	$(S2CTL) apply-pack $(PACKS)
	$(S2CTL) seed-demo

sim: ## Start the telemetry simulator
	$(COMPOSE) --profile sim up -d --build telemetry-sim

sim-stop: ## Stop the telemetry simulator
	$(COMPOSE) --profile sim stop telemetry-sim

token: ## Print an access token for a dev user: make token AS=operator
	@$(S2CTL) token --user $(or $(filter-out as,$(AS)),operator)

executor-role: ## One-time, for a stack created before Phase 2 (run after make up): give s2_executor its dev password
	$(COMPOSE) exec postgres psql -U postgres -d s2 -c "DO \$$\$$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 's2_executor') THEN CREATE ROLE s2_executor; END IF; END \$$\$$" -c "ALTER ROLE s2_executor LOGIN BYPASSRLS PASSWORD '$${S2_EXECUTOR_DB_PASSWORD:-s2_executor_dev}'"
	$(COMPOSE) restart action-executor

dev-markings: ## One-time, for a stack created before markings were enforced: give dev users marking:internal
	$(COMPOSE) exec -T keycloak bash -c '\
		set -e; K=/opt/keycloak/bin/kcadm.sh; \
		$$K config credentials --server http://localhost:8080 --realm master \
			--user "$$KC_BOOTSTRAP_ADMIN_USERNAME" --password "$$KC_BOOTSTRAP_ADMIN_PASSWORD" >/dev/null; \
		$$K get roles/marking:internal -r s2 >/dev/null 2>&1 || \
			$$K create roles -r s2 -s name=marking:internal -s "description=Cleared to read data marked internal"; \
		for user in admin operator supervisor auditor; do $$K add-roles -r s2 --uusername $$user --rolename marking:internal; done; \
		echo "done. Tokens issued before now lack the role: get a new one with make token."'

lakehouse: ## Run the lakehouse CLI in the stack: make lakehouse ARGS="sync CommercialDrone --dry-run"
	@$(COMPOSE) --profile pipelines build --quiet pipelines
	@$(COMPOSE) --profile pipelines run --rm --no-deps --quiet-pull pipelines python -m s2_pipelines $(ARGS)

lakehouse-demo: ## Write the demo asset register to Iceberg and sync it into the ontology
	$(MAKE) --no-print-directory lakehouse ARGS="demo-load"
	$(MAKE) --no-print-directory lakehouse ARGS="sync CommercialDrone"

pipelines-ui: ## Start Dagster (lineage graph, runs, schedule) at http://localhost:3000
	$(COMPOSE) --profile pipelines up -d --build pipelines
	@echo "Dagster: http://localhost:3000"

dev-pipeline-client: ## One-time, for a stack created before the pipeline service account: add it to Keycloak
	$(COMPOSE) exec -T keycloak bash -c '\
		set -e; K=/opt/keycloak/bin/kcadm.sh; \
		$$K config credentials --server http://localhost:8080 --realm master \
			--user "$$KC_BOOTSTRAP_ADMIN_USERNAME" --password "$$KC_BOOTSTRAP_ADMIN_PASSWORD" >/dev/null; \
		if [ -z "$$($$K get clients -r s2 -q clientId=s2-pipeline --fields id --format csv --noquotes)" ]; then \
			$$K create clients -r s2 -f - >/dev/null; echo "created client s2-pipeline"; \
		else cat >/dev/null; echo "client s2-pipeline already exists"; fi; \
		$$K add-roles -r s2 --uusername service-account-s2-pipeline \
			--rolename ontology-admin --rolename marking:internal --rolename viewer; \
		echo "done: pipeline syncs now act as service-account-s2-pipeline"' \
		< deploy/docker-compose/keycloak-clients/s2-pipeline.json

outbox: ## Show the write-back outbox: what was delivered, what is waiting, what is dead
	$(COMPOSE) exec postgres psql -U postgres -d s2 -c "SELECT id, connector, operation, status, attempts, delivered_at, left(last_error, 60) AS last_error FROM ontology.writeback_outbox ORDER BY created_at DESC LIMIT 20"

psql: ## Open psql on the ontology database
	$(COMPOSE) exec postgres psql -U postgres -d s2

# ---------------------------------------------------------------- development

sync: ## Install the Python workspace into .venv
	uv sync

lint: ## Lint and check formatting
	uv run ruff check .
	uv run ruff format --check .

fmt: ## Format and apply safe lint fixes
	uv run ruff check --fix .
	uv run ruff format .

test: ## Run the tests (database and policy tests skip unless configured, see tests/conftest.py)
	uv run pytest

test-stack: ## Run every test against the running Compose stack (after `make up`)
	$(STACK_TEST_ENV) uv run pytest

policy-test: ## Run the Rego unit tests (needs the opa binary)
	opa test policies -v

ui: ## Workshop UI dev server on http://localhost:5173, with hot reload (needs Node 22 and pnpm; stop the workshop-ui container first)
	pnpm --filter workshop-ui dev

ui-test: ## Run the Workshop UI tests
	pnpm --filter workshop-ui test
