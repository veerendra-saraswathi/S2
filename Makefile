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
			echo "ready: http://localhost:8000/docs"; exit 0; \
		fi; \
		sleep 2; \
	done; \
	echo "the API is not ready after 5 minutes. Run 'make ps' and 'make logs'."; exit 1

down: ## Stop the stack (data is kept)
	$(COMPOSE) --profile sim down

nuke: ## Stop the stack and delete all local data
	$(COMPOSE) --profile sim down -v

ps: ## Show service status
	$(COMPOSE) --profile sim ps

logs: ## Follow the logs of the S2 services
	$(COMPOSE) --profile sim logs -f --tail=100 migrate ontology-engine mqtt-bridge timeseries-sink

seed: ## Apply the ontology packs and create demo objects
	$(S2CTL) apply-pack $(PACKS)
	$(S2CTL) seed-demo

sim: ## Start the telemetry simulator
	$(COMPOSE) --profile sim up -d --build telemetry-sim

sim-stop: ## Stop the telemetry simulator
	$(COMPOSE) --profile sim stop telemetry-sim

token: ## Print an access token for a dev user: make token AS=operator
	@$(S2CTL) token --user $(or $(filter-out as,$(AS)),operator)

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

ui: ## Start the Workshop UI dev server on http://localhost:5173
	pnpm --filter workshop-ui dev
