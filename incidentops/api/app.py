"""FastAPI application factory."""

# No ``from __future__ import annotations``: FastAPI resolves the locally
# defined dependency aliases at runtime.

from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import httpx
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
from incidentops.api import benchmarks as benchmark_queries
from incidentops.api import history as history_queries
from incidentops.api import incidents as incident_queries
from incidentops.api import investigations as run_queries
from incidentops.api import metrics as metric_queries
from incidentops.auth import service as auth
from incidentops.auth.service import READ_ROLES, AuthenticatedUser
from incidentops.chat import service as chat
from incidentops.config import Profile, Settings, get_settings
from incidentops.domain.catalog import load_catalog
from incidentops.domain.disposition import Disposition
from incidentops.domain.state_machines import INCIDENT_MACHINE, IncidentState
from incidentops.handling import recheck as rechecks
from incidentops.handling import service as handling
from incidentops.ingestion.alertmanager import PayloadRejectedError, normalize
from incidentops.ingestion.service import ingest_events
from incidentops.persistence.db import make_engine, make_session_factory
from incidentops.security.identities import IdentityRegistry, Role
from incidentops.time import utcnow
from incidentops.tools.backends import HttpTelemetryBackend, TelemetryBackend
from incidentops.verification.verifier import load_verify_config
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


class HandoverRequest(BaseModel):
    expected_version: int = Field(ge=1)


class CloseRequest(BaseModel):
    expected_version: int = Field(ge=1)
    disposition: Disposition
    note: str | None = Field(default=None, max_length=2000)


class ChatQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=chat.MAX_QUESTION_CHARS)


class HandlingResponse(BaseModel):
    outcome: str
    version: int | None


_HTTP_FOR_HANDLING = {
    handling.HandlingOutcome.FORBIDDEN: status.HTTP_403_FORBIDDEN,
    handling.HandlingOutcome.NOT_FOUND: status.HTTP_404_NOT_FOUND,
}


class IngestResponse(BaseModel):
    accepted_alerts: int
    created_incident_ids: list[str]
    updated_incident_ids: list[str]
    ignored_resolved: int


def create_app(
    settings: Settings | None = None,
    session_factory: sessionmaker[Session] | None = None,
    telemetry: TelemetryBackend | None = None,
    benchmark_dir: Path | None = None,
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
    if telemetry is None:
        # Read-only: Prometheus queries only, no lab token (re-check, metric panels).
        telemetry = HttpTelemetryBackend(
            httpx.AsyncClient(timeout=10.0),
            resolved.prometheus_url,
            resolved.loki_url,
            resolved.tempo_url,
            None,
        )
    backend: TelemetryBackend = telemetry
    verify_config = load_verify_config()

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
        # Every read requires an authenticated session with a read role; the
        # anonymous dev-profile exception of ADR-016 was retired in P4.
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
        if user.role not in READ_ROLES:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "read access denied")

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

    def _handling_response(result: handling.HandlingResult) -> HandlingResponse:
        if not result.ok:
            code = _HTTP_FOR_HANDLING.get(result.outcome, status.HTTP_409_CONFLICT)
            raise HTTPException(code, {"outcome": result.outcome.value, "version": result.version})
        return HandlingResponse(outcome=result.outcome.value, version=result.version)

    @app.post("/api/v1/incidents/{incident_id}/assign", tags=["handling"])
    def assign_incident(
        incident_id: str, body: HandoverRequest, user: User, factory: Sessions
    ) -> HandlingResponse:
        with factory.begin() as session:
            result = handling.assign(session, incident_id, user, body.expected_version)
        return _handling_response(result)

    @app.post("/api/v1/incidents/{incident_id}/close", tags=["handling"])
    def close_incident(
        incident_id: str, body: CloseRequest, user: User, factory: Sessions
    ) -> HandlingResponse:
        with factory.begin() as session:
            result = handling.close(
                session, incident_id, user, body.expected_version, body.disposition, body.note
            )
        return _handling_response(result)

    @app.post("/api/v1/incidents/{incident_id}/recheck", tags=["handling"])
    async def recheck_incident(
        incident_id: str, user: User, factory: Sessions
    ) -> rechecks.RecheckResult:
        if user.role not in auth.OPERATE_ROLES:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "operator role required")
        with factory() as session:
            plan = rechecks.plan_recheck(session, incident_id, catalog, verify_config)
        if plan is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
        checked_at = utcnow()
        checks = await rechecks.run_checks(plan, catalog, backend, checked_at)
        outcome = rechecks.outcome_of(checks)
        with factory.begin() as session:
            rechecks.record_recheck(session, plan, user, outcome)
            cleared = rechecks.alert_cleared_at(session, incident_id)
        return rechecks.RecheckResult(
            incident_id=incident_id,
            outcome=outcome,
            checks=checks.checks,
            checked_at=checked_at,
            based_on=plan.action_type or "service_defaults",
            alert_cleared_at=cleared,
        )

    @app.get(
        "/api/v1/incidents/{incident_id}/chat",
        tags=["chat"],
        dependencies=[Depends(require_viewer)],
    )
    def incident_chat(incident_id: str, factory: Sessions) -> chat.ChatThreadView:
        with factory() as session:
            view = chat.thread(session, incident_id)
        if view is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
        return view

    @app.post(
        "/api/v1/incidents/{incident_id}/chat",
        tags=["chat"],
        status_code=status.HTTP_202_ACCEPTED,
    )
    def ask_incident_chat(
        incident_id: str, body: ChatQuestion, user: User, factory: Sessions
    ) -> chat.ChatMessageView:
        # Any signed-in role may ask: answers are read-only and change nothing.
        with factory.begin() as session:
            message = chat.ask(session, incident_id, user, body.question)
        if message is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
        return message

    @app.get("/api/v1/history", tags=["history"], dependencies=[Depends(require_viewer)])
    def incident_history(
        factory: Sessions,
        q: Annotated[str | None, Query(max_length=200)] = None,
        outcome: history_queries.Outcome | None = None,
        service_id: Annotated[str | None, Query(max_length=63)] = None,
        root_cause_id: Annotated[str | None, Query(max_length=64)] = None,
        confidence: Annotated[str | None, Query(max_length=16)] = None,
        since: datetime | None = None,
        page: Annotated[int, Query(ge=1, le=10_000)] = 1,
        page_size: Annotated[int, Query(ge=1, le=200)] = 25,
    ) -> history_queries.HistoryPage:
        flt = history_queries.HistoryFilter(
            q=q or None,
            outcome=outcome,
            service_id=service_id or None,
            root_cause_id=root_cause_id or None,
            confidence=confidence or None,
            since=since,
        )
        with factory() as session:
            return history_queries.search(session, flt, page, page_size, utcnow())

    @app.get(
        "/api/v1/incidents/{incident_id}/metrics",
        tags=["metrics"],
        dependencies=[Depends(require_viewer)],
    )
    async def incident_metrics(
        incident_id: str, factory: Sessions
    ) -> metric_queries.IncidentMetricsView:
        with factory() as session:
            incident = incident_queries.get_incident(session, incident_id)
        if incident is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
        now = utcnow()
        start, end, markers = metric_queries.incident_metric_window(incident, now)
        panel_id = metric_queries.incident_panel_id(incident.service_id, incident.title)
        step = max(5, int((end - start).total_seconds()) // 180)
        panels = await metric_queries.load_panels(
            backend, catalog, start, end, step, {panel_id} if panel_id else set()
        )
        return metric_queries.IncidentMetricsView(
            start=start,
            end=end,
            step_seconds=step,
            grafana_dashboard_url=resolved.grafana_url.rstrip("/") + metric_queries.DASHBOARD_PATH,
            panels=panels,
            incidents=[],
            markers=markers,
            mode="historical" if INCIDENT_MACHINE.is_terminal(incident.state) else "live",
        )

    @app.get("/api/v1/metrics", tags=["metrics"], dependencies=[Depends(require_viewer)])
    async def live_metrics(
        factory: Sessions, minutes: Annotated[int, Query()] = 30
    ) -> metric_queries.MetricsView:
        if minutes not in metric_queries.RANGES:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"minutes must be one of {list(metric_queries.RANGES)}",
            )
        start, end = metric_queries.window(minutes, utcnow())
        step = metric_queries.step_for(minutes)
        panels = await metric_queries.load_panels(backend, catalog, start, end, step)
        with factory() as session:
            windows = metric_queries.incident_windows(session, start, end)
        return metric_queries.MetricsView(
            start=start,
            end=end,
            step_seconds=step,
            grafana_dashboard_url=resolved.grafana_url.rstrip("/") + metric_queries.DASHBOARD_PATH,
            panels=panels,
            incidents=windows,
        )

    @app.get("/api/v1/benchmarks", tags=["benchmarks"], dependencies=[Depends(require_viewer)])
    def benchmark_runs() -> list[benchmark_queries.RunView]:
        return benchmark_queries.list_runs(benchmark_dir or benchmark_queries.DEFAULT_RESULTS_DIR)

    return app
