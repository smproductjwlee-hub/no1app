from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import auth, curriculum, health, i18n, meta, pages, workspaces
from app.core.config import get_settings
from app.db.sqlite import init_db
from app.ws import comm


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(health.router)
    application.include_router(pages.router)
    application.include_router(workspaces.router, prefix="/api/v1")
    application.include_router(auth.router, prefix="/api/v1")
    application.include_router(curriculum.router, prefix="/api/v1")
    application.include_router(meta.router, prefix="/api/v1")
    application.include_router(i18n.router, prefix="/api/v1")
    application.include_router(comm.router, prefix="/api/v1")
    return application


app = create_app()
