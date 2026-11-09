"""FastAPI application factory."""

# No ``from __future__ import annotations``: FastAPI resolves the locally
# defined dependency aliases at runtime.

from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from incidentops import __version__
from incidentops.api import incidents as incident_queries
from incidentops.config import Profile, Settings, get_settings
from incidentops.domain.state_machines import IncidentState
from incidentops.ingestion.alertmanager import PayloadRejectedError, normalize
from incidentops.ingestion.service import ingest_events
from incidentops.persistence.db import make_engine, make_session_factory
from incidentops.security.identities import IdentityRegistry, Role
from incidentops.time import utcnow


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

    def require_viewer() -> None:
        # ADR-016: until session auth lands in P3, incident reads are open only
        # in the local dev profile and refused everywhere else.
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

    return app
