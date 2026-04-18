from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

router = APIRouter(tags=["pages"])


@router.get("/")
async def root_redirect() -> RedirectResponse:
    """브라우저에서 / 만 열었을 때 404 JSON 대신 로그인으로 보냄."""
    return RedirectResponse(url="/login", status_code=302)


@router.get("/api/login")
async def redirect_mistake_api_login() -> RedirectResponse:
    """흔한 오타: /api/login → /login (HTML 로그인은 API 경로가 아님)."""
    return RedirectResponse(url="/login", status_code=302)


@router.get("/api/v1/login")
async def redirect_mistake_api_v1_login() -> RedirectResponse:
    """흔한 오타: /api/v1/login → /login"""
    return RedirectResponse(url="/login", status_code=302)


@router.get("/login.html")
async def redirect_login_html() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=302)


@router.get("/enter")
async def enter_links_page() -> HTMLResponse:
    """CDN 없이 링크만 표시. /login 이 JSON 404로 나오는지 구분할 때 사용."""
    html = """<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>WorkBridge — 입구</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 22rem; margin: 2rem auto; padding: 0 1rem; line-height: 1.5; }
    a {
      display: block; margin: 0.65rem 0; padding: 0.85rem 1rem;
      background: #312e81; color: #fff; text-align: center; border-radius: 0.75rem;
      text-decoration: none; font-weight: 600; font-size: 0.95rem;
    }
    a.secondary { background: #374151; font-weight: 500; font-size: 0.85rem; }
    p.note { font-size: 0.8rem; color: #6b7280; margin-top: 1.25rem; }
    code { font-size: 0.75rem; background: #f3f4f6; padding: 0.15rem 0.35rem; border-radius: 0.25rem; }
  </style>
</head>
<body>
  <h1 style="font-size:1.15rem;">WorkBridge</h1>
  <p>아래를 누르면 같은 서버의 로그인 화면으로 갑니다.</p>
  <a href="/login">통합 로그인</a>
  <a href="/login?role=admin">관리자 로그인</a>
  <a href="/login?role=worker">스태프 로그인</a>
  <a class="secondary" href="/health">서버 확인 (JSON)</a>
  <p class="note">
    이 페이지가 안 보이면 <strong>8000 포트가 다른 프로그램</strong>일 수 있습니다.
    터미널에서 <code>uvicorn main:app --reload --host 0.0.0.0 --port 8000</code> 을
    <code>d:\\kaigo_project</code> 에서 실행했는지 확인하세요.
  </p>
</body>
</html>"""
    return HTMLResponse(content=html)


# app/api/routes/pages.py -> project root / static
_STATIC = Path(__file__).resolve().parent.parent.parent.parent / "static"


def _read_html(name: str) -> str:
    return (_STATIC / name).read_text(encoding="utf-8")


def _admin_html() -> HTMLResponse:
    return HTMLResponse(content=_read_html("admin.html"))


def _worker_html() -> HTMLResponse:
    return HTMLResponse(content=_read_html("worker.html"))


def _login_html() -> HTMLResponse:
    return HTMLResponse(content=_read_html("login.html"))


def _super_html() -> HTMLResponse:
    return HTMLResponse(content=_read_html("super.html"))


def _admin_i18n_js() -> FileResponse:
    return FileResponse(_STATIC / "admin-i18n.js", media_type="application/javascript")


@router.get("/static/admin-i18n.js")
async def admin_i18n_js() -> FileResponse:
    return _admin_i18n_js()


@router.get("/super")
async def super_selector_page() -> HTMLResponse:
    return _super_html()


@router.get("/super/")
async def super_selector_page_slash() -> HTMLResponse:
    return _super_html()


@router.get("/login")
async def login_page() -> HTMLResponse:
    return _login_html()


@router.get("/login/")
async def login_page_slash() -> HTMLResponse:
    return _login_html()


@router.get("/admin")
async def admin_page() -> HTMLResponse:
    return _admin_html()


@router.get("/admin/")
async def admin_page_slash() -> HTMLResponse:
    return _admin_html()


@router.get("/worker")
async def worker_page() -> HTMLResponse:
    return _worker_html()


@router.get("/worker/")
async def worker_page_slash() -> HTMLResponse:
    return _worker_html()
