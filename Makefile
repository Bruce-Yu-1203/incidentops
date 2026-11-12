# Developer entry points. `make check` is the gate required by constitute.md.
.PHONY: web-install web-check security-suite recovery-suite benchmark demo-up demo-down demo-status
.PHONY: env install lint format typecheck test test-unit check db-up db-down migrate seed-users db-logins lab-up lab-down lab-reset lab-smoke lab-lock infra-secrets infra-up infra-down api-run worker-run chat-run control-run executor-run verifier-run p1-gate p2-live p2-gate p3-gate

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
	set -a && . ./.env && set +a && uv run alembic upgrade head

seed-users:
	set -a && . ./.env && set +a && uv run alembic upgrade head && uv run python -m incidentops.cli seed-demo-users && uv run python scripts/create_db_logins.py

db-logins:
	set -a && . ./.env && set +a && uv run alembic upgrade head && uv run python scripts/create_db_logins.py

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

# Investigation worker. MODEL=... overrides INCIDENTOPS_MODEL_ID; with neither
# set, the no-key rules baseline (rules-baseline-v1) is used.
worker-run:
	$(LOAD_ENV) && export INCIDENTOPS_MODEL_ID="$${MODEL:-$${INCIDENTOPS_MODEL_ID:-rules-baseline-v1}}" && $(STRIP_EXECUTOR) && $(call DB_AS,io_agent,AGENT) uv run python -m incidentops.workers.investigation

# Chat agent worker: same read-only agent identity, its own queue (ADR-023).
chat-run:
	$(LOAD_ENV) && export INCIDENTOPS_MODEL_ID="$${MODEL:-$${INCIDENTOPS_MODEL_ID:-rules-baseline-v1}}" && $(STRIP_EXECUTOR) && $(call DB_AS,io_agent,AGENT) uv run python -m incidentops.workers.chat

# Roadmap P2 gate with a real model (needs api-run and worker-run in other terminals).
p2-gate:
	set -a && . ./.env && set +a && P2_GATE_MODEL_CONFIGURED=1 INCIDENTOPS_REQUIRE_LAB=1 uv run pytest -s -m lab tests/e2e/test_p2_agent_gate.py

# Roadmap P3 gate: closed loop with real approval (needs api/control/executor/verifier
# and an investigation worker running; the rules baseline needs no model key).
p3-gate:
	set -a && . ./.env && set +a && INCIDENTOPS_REQUIRE_LAB=1 uv run pytest -s -m lab tests/e2e/test_p3_closed_loop.py

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

# Each process receives only its own credentials: foreign variables are
# stripped here and the process refuses to start if any remain.
LOAD_ENV = set -a && . ./.env && set +a
# Each process connects with its own least-privilege login (migration 0004).
DB_AS = INCIDENTOPS_CONTROL_DATABASE_URL=postgresql+psycopg://$(1):$$INCIDENTOPS_DB_$(2)_PASSWORD@localhost:5433/incidentops_control
STRIP_EXECUTOR = unset INCIDENTOPS_EXECUTOR_LAB_TOKEN FAULT_LAB_EXECUTOR_TOKEN FAULT_LAB_BENCHMARK_TOKEN
STRIP_MODEL = unset INCIDENTOPS_ANTHROPIC_API_KEY

# Run the control-plane API on the host for Alertmanager (host.docker.internal:8000).
api-run:
	$(LOAD_ENV) && $(STRIP_EXECUTOR) && $(STRIP_MODEL) && uv run alembic upgrade head && $(call DB_AS,io_api,API) uv run uvicorn --factory incidentops.api.app:create_app --host 127.0.0.1 --port 8000

control-run:
	$(LOAD_ENV) && $(STRIP_EXECUTOR) && $(STRIP_MODEL) && $(call DB_AS,io_api,API) uv run python -m incidentops.workers.control

executor-run:
	$(LOAD_ENV) && $(STRIP_MODEL) && unset INCIDENTOPS_LAB_READ_TOKEN FAULT_LAB_READ_TOKEN FAULT_LAB_BENCHMARK_TOKEN && $(call DB_AS,io_executor,EXECUTOR) uv run python -m incidentops.workers.executor

verifier-run:
	$(LOAD_ENV) && $(STRIP_EXECUTOR) && $(STRIP_MODEL) && $(call DB_AS,io_verifier,VERIFIER) uv run python -m incidentops.workers.verifier

# Release-gate suites (benchmark.md §8, §9). They run inside `make check` too.
security-suite:
	INCIDENTOPS_REQUIRE_DB=1 uv run pytest -m security -v

recovery-suite:
	INCIDENTOPS_REQUIRE_DB=1 uv run pytest -m recovery -v

# Live benchmark (benchmark.md §4). Needs lab-up, infra-up, seed-users and the
# API plus all workers running. Examples:
#   make benchmark CASES=B-F03 TRIALS=5 VARIANT=B0_RULES
#   make benchmark TRIALS=1                      # all seven cases
CASES ?=
TRIALS ?= 1
VARIANT ?= B0_RULES
benchmark:
	set -a && . ./.env && set +a && uv run python -m fault_lab.benchmark run --cases "$(CASES)" --trials $(TRIALS) --variant $(VARIANT)

# One-command local demo (scripts/demo.sh): infrastructure, lab, telemetry,
# API, all workers and the web UI in the background; logs in .run/logs.
demo-up:
	scripts/demo.sh up

demo-down:
	scripts/demo.sh down

demo-status:
	scripts/demo.sh status
