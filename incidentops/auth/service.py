"""User and session management with server-side role checks."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from incidentops.auth.passwords import hash_password, verify_password
from incidentops.domain.ids import new_id
from incidentops.persistence.models import AuditEventRow, SessionRow, UserRow
from incidentops.time import utcnow


class HumanRole(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    APPROVER = "approver"
    ADMINISTRATOR = "administrator"


# Read access is cumulative; mutating operations list their roles explicitly so
# no role gains a hidden approval capability (requirements §4).
READ_ROLES = frozenset(HumanRole)
OPERATE_ROLES = frozenset({HumanRole.OPERATOR, HumanRole.APPROVER, HumanRole.ADMINISTRATOR})
APPROVE_ROLES = frozenset({HumanRole.APPROVER})


@dataclass(frozen=True)
class AuthenticatedUser:
    id: str
    username: str
    role: HumanRole

    @property
    def principal(self) -> str:
        return f"user:{self.username}"


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_user(session: Session, username: str, password: str, role: HumanRole) -> str:
    user_id = new_id("usr")
    session.add(
        UserRow(id=user_id, username=username, password_hash=hash_password(password), role=role)
    )
    session.add(
        AuditEventRow(
            id=new_id("aud"),
            actor="service:admin-cli",
            event_type="user_created",
            resource_type="users",
            resource_id=user_id,
            decision=role.value,
            correlation_ids={"username": username},
        )
    )
    return user_id


def login(
    session: Session, username: str, password: str, ttl: timedelta, now: datetime | None = None
) -> tuple[str, AuthenticatedUser] | None:
    """Verify credentials and return ``(token, user)``; the token is shown only once."""
    user = session.scalars(select(UserRow).where(UserRow.username == username)).one_or_none()
    valid = user is not None and user.active and verify_password(password, user.password_hash)
    session.add(
        AuditEventRow(
            id=new_id("aud"),
            actor=f"user:{username}"[:128],
            event_type="login",
            resource_type="users",
            resource_id=user.id if user else "unknown",
            decision="success" if valid else "failure",
            correlation_ids={},
        )
    )
    if not valid or user is None:
        return None
    token = secrets.token_urlsafe(32)
    session.add(
        SessionRow(
            id=new_id("ses"),
            token_hash=_token_hash(token),
            user_id=user.id,
            expires_at=(now or utcnow()) + ttl,
        )
    )
    return token, AuthenticatedUser(user.id, user.username, HumanRole(user.role))


def resolve(session: Session, token: str, now: datetime | None = None) -> AuthenticatedUser | None:
    row = session.execute(
        select(SessionRow, UserRow)
        .join(UserRow, UserRow.id == SessionRow.user_id)
        .where(SessionRow.token_hash == _token_hash(token))
    ).one_or_none()
    if row is None:
        return None
    sess, user = row
    if sess.revoked_at is not None or sess.expires_at <= (now or utcnow()) or not user.active:
        return None
    return AuthenticatedUser(user.id, user.username, HumanRole(user.role))


def logout(session: Session, token: str) -> None:
    session.execute(
        update(SessionRow)
        .where(SessionRow.token_hash == _token_hash(token), SessionRow.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
