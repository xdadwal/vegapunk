from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

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

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
