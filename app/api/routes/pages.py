from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["pages"])

# app/api/routes/pages.py -> project root / static
_STATIC = Path(__file__).resolve().parent.parent.parent.parent / "static"


def _admin_file() -> FileResponse:
    return FileResponse(_STATIC / "admin.html")


def _worker_file() -> FileResponse:
    return FileResponse(_STATIC / "worker.html")


def _login_file() -> FileResponse:
    return FileResponse(_STATIC / "login.html")


def _super_file() -> FileResponse:
    return FileResponse(_STATIC / "super.html")


@router.get("/super")
async def super_selector_page() -> FileResponse:
    return _super_file()


@router.get("/super/")
async def super_selector_page_slash() -> FileResponse:
    return _super_file()


@router.get("/login")
async def login_page() -> FileResponse:
    return _login_file()


@router.get("/login/")
async def login_page_slash() -> FileResponse:
    return _login_file()


@router.get("/admin")
async def admin_page() -> FileResponse:
    return _admin_file()


@router.get("/admin/")
async def admin_page_slash() -> FileResponse:
    return _admin_file()


@router.get("/worker")
async def worker_page() -> FileResponse:
    return _worker_file()


@router.get("/worker/")
async def worker_page_slash() -> FileResponse:
    return _worker_file()
