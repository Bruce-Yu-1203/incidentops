# Developer entry points. `make check` is the gate required by constitute.md.
.PHONY: install lint format typecheck test test-unit check db-up db-down migrate

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
	INCIDENTOPS_REQUIRE_DB=1 uv run pytest

test-unit:
	uv run pytest tests/unit

check: lint typecheck test

db-up:
	docker compose up -d --wait control-postgres

db-down:
	docker compose down

migrate:
	uv run alembic upgrade head
