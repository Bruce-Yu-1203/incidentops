"""FastAPI application factory."""

# No ``from __future__ import annotations``: FastAPI resolves the locally
# defined dependency aliases at runtime.

from datetime import timedelta
from typing import Annotated, Any

from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from incidentops import __version__
from incidentops.actions import approvals
from incidentops.api import actions as action_queries
from incidentops.api import incidents as incident_queries
from incidentops.api import investigations as run_queries
from incidentops.auth import service as auth
from incidentops.auth.service import READ_ROLES, AuthenticatedUser
from incidentops.config import Profile, Settings, get_settings
from incidentops.domain.catalog import load_catalog
from incidentops.domain.state_machines import IncidentState
from incidentops.ingestion.alertmanager import PayloadRejectedError, normalize
from incidentops.ingestion.service import ingest_events
from incidentops.persistence.db import make_engine, make_session_factory
from incidentops.security.identities import IdentityRegistry, Role
from incidentops.time import utcnow
from incidentops.workers.credentials import assert_credential_scope

SESSION_COOKIE = "incidentops_session"


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserView(BaseModel):
    username: str
    role: str


class ApproveRequest(BaseModel):
    action_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    expected_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=2000)


class DecisionRequest(BaseModel):
    expected_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=2000)


class DecisionResponse(BaseModel):
    outcome: str
    action_state: str | None


_HTTP_FOR_OUTCOME = {
    approvals.Outcome.FORBIDDEN: status.HTTP_403_FORBIDDEN,
    approvals.Outcome.SEPARATION_OF_DUTIES: status.HTTP_403_FORBIDDEN,
    approvals.Outcome.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    approvals.Outcome.STATE_CONFLICT: status.HTTP_409_CONFLICT,
    approvals.Outcome.VERSION_CONFLICT: status.HTTP_409_CONFLICT,
    approvals.Outcome.HASH_MISMATCH: status.HTTP_409_CONFLICT,
    approvals.Outcome.EXPIRED: status.HTTP_409_CONFLICT,
}


class IngestResponse(BaseModel):
    accepted_alerts: int
    created_incident_ids: list[str]
    updated_incident_ids: list[str]
    ignored_resolved: int


def create_app(
    settings: Settings | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> FastAPI:
    """Build the control-plane API application.

    Handlers stay thin: authenticate, validate, run one transaction, and leave
    durable work to workers through the outbox.
    """
    resolved = settings or get_settings()
    assert_credential_scope(Role.API, resolved)
    app = FastAPI(title="IncidentOps Control Plane", version=__version__)
    app.state.settings = resolved
    app.state.session_factory = session_factory
    tokens: dict[Role, str] = {}
    if resolved.alertmanager_token is not None:
        tokens[Role.ALERTMANAGER] = resolved.alertmanager_token.get_secret_value()
    registry = IdentityRegistry.from_tokens(tokens)

    def sessions(request: Request) -> sessionmaker[Session]:
        factory: sessionmaker[Session] | None = request.app.state.session_factory
        if factory is None:
            # Created lazily so liveness works without a database.
            engine = make_engine(resolved.control_database_url.get_secret_value())
            factory = make_session_factory(engine)
            request.app.state.session_factory = factory
        return factory

    Sessions = Annotated[sessionmaker[Session], Depends(sessions)]  # noqa: N806 - type alias

    def require_alertmanager(authorization: Annotated[str | None, Header()] = None) -> Role:
        if resolved.alertmanager_token is None:
            # Fail closed: an unconfigured webhook secret accepts nothing.
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "webhook not configured")
        token = authorization.removeprefix("Bearer ") if authorization else None
        if registry.authenticate(token) is not Role.ALERTMANAGER:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid webhook credentials")
        return Role.ALERTMANAGER

    def current_user(
        request: Request,
        session_cookie: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> AuthenticatedUser | None:
        token = session_cookie
        if authorization and authorization.startswith("Session "):
            token = authorization.removeprefix("Session ")
        if not token:
            return None
        with sessions(request)() as session:
            return auth.resolve(session, token)

    MaybeUser = Annotated[AuthenticatedUser | None, Depends(current_user)]  # noqa: N806

    def require_user(user: MaybeUser) -> AuthenticatedUser:
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
        return user

    User = Annotated[AuthenticatedUser, Depends(require_user)]  # noqa: N806

    def require_viewer(user: MaybeUser) -> None:
        if user is not None and user.role in READ_ROLES:
            return
        # ADR-016: anonymous reads remain possible only in the local dev profile.
        if resolved.profile is not Profile.DEV:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        # Liveness only: never touches dependencies so it stays cheap.
        return {"status": "ok", "version": __version__, "profile": resolved.profile.value}

    @app.post(
        "/api/v1/alerts/alertmanager",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=IngestResponse,
        tags=["ingestion"],
    )
    def alertmanager_webhook(
        body: dict[str, Any],
        factory: Sessions,
        _: Annotated[Role, Depends(require_alertmanager)],
    ) -> IngestResponse:
        try:
            events = normalize(body, received_at=utcnow())
        except PayloadRejectedError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        result = ingest_events(factory, events, actor=f"service:{Role.ALERTMANAGER.value}")
        return IngestResponse(
            accepted_alerts=len(events),
            created_incident_ids=result.created_incident_ids,
            updated_incident_ids=result.updated_incident_ids,
            ignored_resolved=result.ignored_resolved,
        )

    @app.get("/api/v1/incidents", tags=["incidents"], dependencies=[Depends(require_viewer)])
    def list_incidents(
        factory: Sessions,
        state: IncidentState | None = None,
        service_id: Annotated[str | None, Query(max_length=63)] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> list[incident_queries.IncidentSummary]:
        with factory() as session:
            return incident_queries.list_incidents(session, state, service_id, limit)

    @app.get(
        "/api/v1/incidents/{incident_id}",
        tags=["incidents"],
        dependencies=[Depends(require_viewer)],
    )
    def get_incident(incident_id: str, factory: Sessions) -> incident_queries.IncidentDetail:
        with factory() as session:
            detail = incident_queries.get_incident(session, incident_id)
        if detail is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
        return detail

    catalog = load_catalog()

    @app.post("/api/v1/auth/login", tags=["auth"])
    def login_endpoint(body: LoginRequest, response: Response, factory: Sessions) -> UserView:
        with factory.begin() as session:
            result = auth.login(
                session, body.username, body.password, timedelta(hours=resolved.session_ttl_hours)
            )
        if result is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
        token, user = result
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=resolved.session_ttl_hours * 3600,
            httponly=True,
            samesite="strict",
            secure=resolved.profile is not Profile.DEV,
        )
        # The token is also returned so the server-rendered UI can forward it.
        response.headers["X-Session-Token"] = token
        return UserView(username=user.username, role=user.role.value)

    @app.post("/api/v1/auth/logout", tags=["auth"], status_code=status.HTTP_204_NO_CONTENT)
    def logout_endpoint(
        response: Response,
        factory: Sessions,
        session_cookie: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        token = session_cookie
        if authorization and authorization.startswith("Session "):
            token = authorization.removeprefix("Session ")
        if token:
            with factory.begin() as session:
                auth.logout(session, token)
        response.delete_cookie(SESSION_COOKIE)

    @app.get("/api/v1/auth/me", tags=["auth"])
    def me(user: User) -> UserView:
        return UserView(username=user.username, role=user.role.value)

    @app.get(
        "/api/v1/actions/{action_id}", tags=["actions"], dependencies=[Depends(require_viewer)]
    )
    def get_action(action_id: str, factory: Sessions) -> action_queries.ActionDetail:
        with factory() as session:
            detail = action_queries.get_action(session, action_id)
        if detail is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "action not found")
        return detail

    def _decision_response(result: approvals.DecisionResult) -> DecisionResponse:
        if not result.ok:
            code = _HTTP_FOR_OUTCOME.get(result.outcome, status.HTTP_409_CONFLICT)
            raise HTTPException(
                code, {"outcome": result.outcome.value, "state": result.action_state}
            )
        return DecisionResponse(outcome=result.outcome.value, action_state=result.action_state)

    @app.post("/api/v1/actions/{action_id}/approve", tags=["actions"])
    def approve_action(
        action_id: str, body: ApproveRequest, user: User, factory: Sessions
    ) -> DecisionResponse:
        # Expiry transitions must persist even though the request fails, so the
        # transaction commits whatever the decision function wrote.
        with factory.begin() as session:
            result = approvals.approve(
                session,
                action_id,
                user,
                body.action_hash,
                body.expected_version,
                utcnow(),
                reason=body.reason,
                separation_of_duties=resolved.require_separation_of_duties,
            )
            if not result.ok:
                approvals.record_refusal(session, action_id, user, "approve", result)
        return _decision_response(result)

    @app.post("/api/v1/actions/{action_id}/reject", tags=["actions"])
    def reject_action(
        action_id: str, body: DecisionRequest, user: User, factory: Sessions
    ) -> DecisionResponse:
        with factory.begin() as session:
            result = approvals.reject(
                session, action_id, user, body.expected_version, reason=body.reason
            )
            if not result.ok:
                approvals.record_refusal(session, action_id, user, "reject", result)
        return _decision_response(result)

    @app.post("/api/v1/actions/{action_id}/cancel", tags=["actions"])
    def cancel_action(
        action_id: str, body: DecisionRequest, user: User, factory: Sessions
    ) -> DecisionResponse:
        with factory.begin() as session:
            result = approvals.cancel(
                session, action_id, user, body.expected_version, reason=body.reason
            )
            if not result.ok:
                approvals.record_refusal(session, action_id, user, "cancel", result)
        return _decision_response(result)

    @app.get(
        "/api/v1/investigations/{run_id}",
        tags=["investigations"],
        dependencies=[Depends(require_viewer)],
    )
    def get_investigation(run_id: str, factory: Sessions) -> run_queries.RunDetail:
        with factory() as session:
            detail = run_queries.get_run(session, run_id, catalog, resolved.grafana_url)
        if detail is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "investigation not found")
        return detail

    return app
