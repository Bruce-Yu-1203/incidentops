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


def test_incident_groups_migration_down_and_up(engine: Engine, database_url: str) -> None:
    """0012 down releases GROUPED incidents; up restores the group schema."""
    from sqlalchemy import text

    config = alembic_config(database_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO incidents (id, dedup_key, service_id, environment, severity, state)"
                " VALUES ('inc_mig0012', 'fault-lab:lab-api', 'lab-api', 'fault-lab',"
                " 'warning', 'GROUPED')"
            )
        )
    try:
        command.downgrade(config, "0011")
        probe = create_engine(database_url)
        try:
            assert "incident_groups" not in inspect(probe).get_table_names()
            columns = {c["name"] for c in inspect(probe).get_columns("incidents")}
            assert "group_id" not in columns
            with probe.connect() as conn:
                state = conn.execute(
                    text("SELECT state FROM incidents WHERE id = 'inc_mig0012'")
                ).scalar_one()
            assert state == "RECEIVED"
        finally:
            probe.dispose()
    finally:
        command.upgrade(config, "head")
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM incidents WHERE id = 'inc_mig0012'"))
    probe = create_engine(database_url)
    try:
        assert "incident_groups" in inspect(probe).get_table_names()
        columns = {c["name"] for c in inspect(probe).get_columns("incidents")}
        assert {"group_id", "group_split_at", "group_note", "symptom_class"} <= columns
    finally:
        probe.dispose()
