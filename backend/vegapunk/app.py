from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from vegapunk.api.containers import router as containers_router
from vegapunk.api.host import router as host_router
from vegapunk.api.settings import router as settings_router
from vegapunk.auth.routes import router as auth_router
from vegapunk.capture.collectors import Collector, build_collectors
from vegapunk.capture.scheduler import Scheduler
from vegapunk.config import Settings
from vegapunk.crypto import load_or_create_key
from vegapunk.db import connect, migrate, prepare_data_dir


def _default_provider(**kwargs):
    import logpose

    return logpose.resolve("openai-compat", **kwargs)


def create_app(
    settings: Settings,
    collectors: Sequence[Collector] | None = None,
    make_provider=None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.started_at = datetime.now(UTC)
        prepare_data_dir(settings)
        app.state.crypto_key = load_or_create_key(settings.data_dir)
        conn = connect(settings.db_path)
        try:
            migrate(conn)
        finally:
            conn.close()
        scheduler = None
        if settings.capture:
            scheduler = Scheduler(
                settings.db_path,
                collectors if collectors is not None else build_collectors(settings),
                settings.capture_interval,
            )
            scheduler.start()
        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.stop()

    app = FastAPI(title="Vegapunk", lifespan=lifespan)
    app.state.settings = settings
    app.state.make_provider = make_provider or _default_provider
    app.include_router(auth_router)
    app.include_router(host_router)
    app.include_router(containers_router)
    app.include_router(settings_router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    if settings.static_dir is not None:
        _serve_frontend(app, settings.static_dir)

    return app


def _serve_frontend(app: FastAPI, static_dir: Path) -> None:
    """Serve the built frontend. Any other non-API path gets index.html, so refreshing
    a client-side route like /profile works."""
    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404)
        return FileResponse(static_dir / "index.html")
