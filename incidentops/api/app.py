"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from incidentops import __version__
from incidentops.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the control-plane API application.

    Handlers are intentionally thin; durable work is delegated to workers
    through the PostgreSQL outbox (added in later phases).
    """
    resolved = settings or get_settings()
    app = FastAPI(title="IncidentOps Control Plane", version=__version__)
    app.state.settings = resolved

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        # Liveness only: never touches dependencies so it stays cheap.
        return {"status": "ok", "version": __version__, "profile": resolved.profile.value}

    return app
