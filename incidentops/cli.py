"""Administrative CLI (runs with the control-plane database credentials).

    python -m incidentops.cli create-user USERNAME --role approver
    python -m incidentops.cli seed-demo-users

Passwords are read from the environment or prompted for; they are never passed
as command-line arguments, which would leak into shell history and ``ps``.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from sqlalchemy import select

from incidentops.auth.service import HumanRole, create_user
from incidentops.config import get_settings
from incidentops.persistence.db import make_engine, make_session_factory
from incidentops.persistence.models import UserRow

# Demo identities seeded for local runs and benchmarks; passwords come from .env.
DEMO_USERS = {
    "viewer": (HumanRole.VIEWER, "INCIDENTOPS_DEMO_VIEWER_PASSWORD"),
    "operator": (HumanRole.OPERATOR, "INCIDENTOPS_DEMO_OPERATOR_PASSWORD"),
    "approver": (HumanRole.APPROVER, "INCIDENTOPS_DEMO_APPROVER_PASSWORD"),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="incidentops")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-user")
    create.add_argument("username")
    create.add_argument("--role", choices=[r.value for r in HumanRole], required=True)
    sub.add_parser("seed-demo-users")
    args = parser.parse_args(argv)

    factory = make_session_factory(
        make_engine(get_settings().control_database_url.get_secret_value())
    )
    with factory.begin() as session:
        if args.command == "create-user":
            password = os.environ.get("INCIDENTOPS_NEW_USER_PASSWORD") or getpass.getpass()
            create_user(session, args.username, password, HumanRole(args.role))
            print(f"created {args.username} ({args.role})")
            return 0
        for username, (role, variable) in DEMO_USERS.items():
            if session.scalars(select(UserRow).where(UserRow.username == username)).first():
                print(f"{username} exists; skipped")
                continue
            demo_password = os.environ.get(variable)
            if not demo_password:
                print(f"{variable} is not set", file=sys.stderr)
                return 2
            create_user(session, username, demo_password, role)
            print(f"created {username} ({role.value})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
