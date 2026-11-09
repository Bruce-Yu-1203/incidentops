"""Fixtures for integration tests against a real PostgreSQL.

Set INCIDENTOPS_TEST_DATABASE_URL to point at a server where the configured
user may create databases. A dedicated ``*_test`` database is recreated for
every test session. When INCIDENTOPS_REQUIRE_DB=1 (CI and ``make check``) an
unreachable database fails the run instead of skipping it.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from incidentops.persistence.models import Base

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_URL = "postgresql+psycopg://incidentops:incidentops@localhost:5433/incidentops_test"


def _test_url() -> str:
    return os.environ.get("INCIDENTOPS_TEST_DATABASE_URL", DEFAULT_URL)


def _recreate_database(url: str) -> None:
    parsed = make_url(url)
    admin = create_engine(parsed.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            name = parsed.database
            assert name is not None
            assert name.endswith("_test"), "refusing to recreate a non-test database"
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        admin.dispose()


def alembic_config(url: str) -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture(scope="session")
def database_url() -> str:
    url = _test_url()
    try:
        _recreate_database(url)
    except OperationalError as exc:
        if os.environ.get("INCIDENTOPS_REQUIRE_DB") == "1":
            pytest.fail(f"test database unreachable: {exc.orig}")
        pytest.skip("test database unreachable; run `make db-up`")
    return url


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[Engine]:
    command.upgrade(alembic_config(database_url), "head")
    eng = create_engine(database_url, future=True)
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    yield sessionmaker(bind=engine, expire_on_commit=False, future=True)
    tables = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
    with engine.begin() as conn:
        # The append-only trigger also blocks TRUNCATE, so disable it only for
        # test cleanup inside this transaction.
        conn.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_append_only"))
        conn.execute(text(f"TRUNCATE {tables} CASCADE"))
        conn.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_append_only"))
