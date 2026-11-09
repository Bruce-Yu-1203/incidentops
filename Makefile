# Developer entry points. `make check` is the gate required by constitute.md.
.PHONY: web-install web-check
.PHONY: env install lint format typecheck test test-unit check db-up db-down migrate lab-up lab-down lab-reset lab-smoke lab-lock

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
	INCIDENTOPS_REQUIRE_DB=1 uv run pytest -m "not lab"

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

LAB_COMPOSE = docker compose -f fault-lab/compose.yaml --env-file .env

lab-up:
	$(LAB_COMPOSE) up -d --build --wait

lab-down:
	$(LAB_COMPOSE) down -v

# Benchmark-controller operations: they read FAULT_LAB_* tokens from .env.
lab-reset:
	set -a && . ./.env && set +a && uv run python -m fault_lab.scripts.labctl reset

lab-smoke:
	set -a && . ./.env && set +a && INCIDENTOPS_REQUIRE_LAB=1 uv run pytest -m lab tests/e2e

lab-lock:
	uv export --package incidentops-fault-lab --no-dev --frozen --no-emit-workspace --no-header -q -o fault-lab/requirements.lock
