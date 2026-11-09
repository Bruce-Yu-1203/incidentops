"""Migration round-trip and drift checks against real PostgreSQL."""

from __future__ import annotations

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, inspect

from incidentops.persistence.models import Base, include_object
from tests.integration.conftest import alembic_config

pytestmark = pytest.mark.integration


def test_models_match_migrations(engine: Engine) -> None:
    with engine.connect() as conn:
        context = MigrationContext.configure(conn, opts={"include_object": include_object})
        diff = compare_metadata(context, Base.metadata)
    assert diff == [], f"schema drift between models and migrations: {diff}"


def test_downgrade_and_upgrade_round_trip(engine: Engine, database_url: str) -> None:
    config = alembic_config(database_url)
    command.downgrade(config, "base")
    probe = create_engine(database_url)
    try:
        assert "incidents" not in inspect(probe).get_table_names()
    finally:
        probe.dispose()
    command.upgrade(config, "head")
    probe = create_engine(database_url)
    try:
        tables = set(inspect(probe).get_table_names())
    finally:
        probe.dispose()
    assert {t.name for t in Base.metadata.sorted_tables} <= tables
