"""Create (or update) per-process LOGIN users for the least-privilege roles.

Run as the database owner after migrations::

    python scripts/create_db_logins.py

Passwords come from INCIDENTOPS_DB_<ROLE>_PASSWORD variables; each login is a
member of exactly one group role created by migration 0004.
"""

from __future__ import annotations

import os
import sys
from typing import Final

from sqlalchemy import create_engine, text

LOGINS: Final = {
    "io_api": ("incidentops_api", "INCIDENTOPS_DB_API_PASSWORD"),
    "io_agent": ("incidentops_agent", "INCIDENTOPS_DB_AGENT_PASSWORD"),
    "io_executor": ("incidentops_executor", "INCIDENTOPS_DB_EXECUTOR_PASSWORD"),
    "io_verifier": ("incidentops_verifier", "INCIDENTOPS_DB_VERIFIER_PASSWORD"),
}


def ensure_login(conn: object, login: str, group: str, password: str) -> None:
    """Idempotently create ``login`` with ``password`` as a member of ``group``."""
    execute = conn.execute  # type: ignore[attr-defined]
    exists = execute(text("SELECT 1 FROM pg_roles WHERE rolname = :n"), {"n": login}).first()
    # Identifiers come from the fixed LOGINS table; only the password is a value,
    # and it is passed through format() with %L quoting inside PostgreSQL.
    verb = "ALTER" if exists else "CREATE"
    statement = execute(
        text(
            "SELECT format('%s ROLE %I LOGIN PASSWORD %L', CAST(:verb AS text), "
            "CAST(:login AS text), CAST(:password AS text))"
        ),
        {"verb": verb, "login": login, "password": password},
    ).scalar_one()
    execute(text(statement))
    execute(text(f"GRANT {group} TO {login}"))


def main() -> int:
    url = os.environ.get("INCIDENTOPS_CONTROL_DATABASE_URL")
    if not url:
        print("INCIDENTOPS_CONTROL_DATABASE_URL (owner) is required", file=sys.stderr)
        return 2
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        for login, (group, variable) in LOGINS.items():
            password = os.environ.get(variable)
            if not password:
                print(f"{variable} is not set", file=sys.stderr)
                return 2
            ensure_login(conn, login, group, password)
            print(f"login {login} -> {group}")
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
