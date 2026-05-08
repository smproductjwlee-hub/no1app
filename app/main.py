from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException as FastAPIHTTPException, Request
from fastapi.exception_handlers import http_exception_handler as default_http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import auth, curriculum, health, i18n, meta, pages, workspaces
from app.core.config import get_settings
from app.db.sqlite import init_db
from app.ws import comm


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # asyncio.to_thread / FastAPI のスレッドプール容量を拡張。
    # デフォルトは min(32, CPU+4) で、同時 DB 呼び出しが多いと枯渇する。
    # 64 にしておくと数百同時接続時の SQLite 呼び出しが詰まらない。
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=64, thread_name_prefix="wb-db"))
    yield


async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
    """404 시 어떤 경로로 요청했는지와 올바른 로그인 URL을 JSON에 포함 (Starlette 라우팅 404 포함)."""
    if exc.status_code == 404:
        return JSONResponse(
            status_code=404,
            content={
                "detail": "Not Found",
                "path": request.url.path,
                "hint": "로그인 페이지는 HTML 경로입니다: http://127.0.0.1:8000/login 또는 /login?role=admin",
                "note": "/api/v1/... 는 JSON API입니다. 브라우저에서 보는 로그인 화면은 /login 입니다.",
            },
        )
    return await default_http_exception_handler(request, exc)


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
    # 라우팅 404는 Starlette HTTPException, 라우트에서 raise 하는 것은 FastAPI HTTPException(서브클래스)일 수 있음
    application.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    application.add_exception_handler(FastAPIHTTPException, _http_exception_handler)
    return application


app = create_app()
