from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.briefing import router as briefing_router
from app.api.dashboard import router as dashboard_router
from app.api.events import router as events_router
from app.api.health import router as health_router
from app.api.incidents import router as incidents_router
from app.api.ingest import router as ingest_router
from app.api.quality import router as quality_router
from app.core.config import get_settings
from app.core.database import init_db
from app.core.errors import register_exception_handlers


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(ingest_router)
    app.include_router(events_router)
    app.include_router(quality_router)
    app.include_router(incidents_router)
    app.include_router(briefing_router)
    app.include_router(dashboard_router)
    return app


app = create_app()
