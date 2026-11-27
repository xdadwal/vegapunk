from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from vegapunk.auth.routes import router as auth_router
from vegapunk.config import Settings
from vegapunk.db import connect, migrate, prepare_data_dir


def create_app(settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        prepare_data_dir(settings)
        conn = connect(settings.db_path)
        try:
            migrate(conn)
        finally:
            conn.close()
        yield

    app = FastAPI(title="Vegapunk", lifespan=lifespan)
    app.state.settings = settings
    app.include_router(auth_router)

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
