"""Alembic environment: runs migrations against the Control PostgreSQL."""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from incidentops.config import get_settings
from incidentops.persistence.models import Base, include_object

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    # An explicit URL set by tests or tooling wins over environment settings.
    configured = config.get_main_option("sqlalchemy.url")
    if configured:
        return configured
    return get_settings().control_database_url.get_secret_value()


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        include_object=include_object,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
