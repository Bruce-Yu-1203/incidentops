# Developer entry points. `make check` is the gate required by constitute.md.
.PHONY: web-install web-check
.PHONY: env install lint format typecheck test test-unit check db-up db-down migrate seed-users lab-up lab-down lab-reset lab-smoke lab-lock infra-secrets infra-up infra-down api-run worker-run p1-gate p2-live p2-gate

env:
	uv run python scripts/generate_env.py

install:
	uv sync --all-packages

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy

test:
	INCIDENTOPS_REQUIRE_DB=1 INCIDENTOPS_REQUIRE_DOCKER=1 uv run pytest -m "not lab"

test-unit:
	uv run pytest tests/unit

check: lint typecheck test web-check

web-install:
	cd apps/web && pnpm install --frozen-lockfile

web-check:
	cd apps/web && pnpm lint && pnpm typecheck && pnpm test && pnpm build

db-up:
	docker compose up -d --wait control-postgres

db-down:
	docker compose down

migrate:
	uv run alembic upgrade head

seed-users:
	set -a && . ./.env && set +a && uv run alembic upgrade head && uv run python -m incidentops.cli seed-demo-users

LAB_COMPOSE = docker compose -f fault-lab/compose.yaml --env-file .env

lab-up:
	$(LAB_COMPOSE) up -d --build --wait

lab-down:
	$(LAB_COMPOSE) down -v

# Benchmark-controller operations: they read FAULT_LAB_* tokens from .env.
lab-reset:
	set -a && . ./.env && set +a && uv run python -m fault_lab.scripts.labctl reset

lab-smoke:
	set -a && . ./.env && set +a && INCIDENTOPS_REQUIRE_LAB=1 uv run pytest -m lab tests/e2e/test_fault_lab_gate.py

# Investigation worker (needs INCIDENTOPS_MODEL_ID and INCIDENTOPS_ANTHROPIC_API_KEY in .env).
worker-run:
	set -a && . ./.env && set +a && uv run python -m incidentops.workers.investigation

# Roadmap P2 gate with a real model (needs api-run and worker-run in other terminals).
p2-gate:
	set -a && . ./.env && set +a && P2_GATE_MODEL_CONFIGURED=1 INCIDENTOPS_REQUIRE_LAB=1 uv run pytest -s -m lab tests/e2e/test_p2_agent_gate.py

# Roadmap P1 gate: needs lab-up, infra-up and api-run (about 2-5 minutes).
# P2 live check: real read tools against lab telemetry (scripted model, no key needed).
p2-live:
	set -a && . ./.env && set +a && INCIDENTOPS_REQUIRE_LAB=1 uv run pytest -m lab tests/e2e/test_p2_live_tools.py

p1-gate:
	set -a && . ./.env && set +a && INCIDENTOPS_REQUIRE_LAB=1 uv run pytest -m lab tests/e2e/test_exporter_contracts.py tests/e2e/test_p1_alert_gate.py

lab-lock:
	uv export --package incidentops-fault-lab --no-dev --frozen --no-emit-workspace --no-header -q -o fault-lab/requirements.lock

INFRA_COMPOSE = docker compose -f infra/compose.yaml --env-file .env

# Alertmanager reads its webhook bearer token from a file, never from git.
infra-secrets:
	@mkdir -p infra/secrets
	@set -a && . ./.env && set +a && printf '%s' "$$INCIDENTOPS_ALERTMANAGER_TOKEN" > infra/secrets/webhook_token
	@chmod 600 infra/secrets/webhook_token

infra-up: infra-secrets
	$(INFRA_COMPOSE) up -d --wait

infra-down:
	$(INFRA_COMPOSE) down -v

# Run the control-plane API on the host for Alertmanager (host.docker.internal:8000).
api-run:
	set -a && . ./.env && set +a && uv run alembic upgrade head && uv run uvicorn --factory incidentops.api.app:create_app --host 127.0.0.1 --port 8000
