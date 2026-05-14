from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

router = APIRouter(tags=["pages"])


# Phase 2.5 — URL 슬러그 라우팅 정규식 (3계층 멀티테넌시)
# 영문 소문자·숫자·하이픈, 시작/끝은 영숫자, 3-20자
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{1,18}[a-z0-9]$")

# 슬러그로 사용할 수 없는 예약어 (정적 라우트와 충돌 회피)
_RESERVED_SLUGS = {
    "admin", "worker", "super", "login", "distributor", "enter", "api",
    "static", "health", "ws", "favicon", "_super", "robots.txt",
}


def _is_valid_slug(s: str) -> bool:
    if not s or s in _RESERVED_SLUGS:
        return False
    return bool(_SLUG_RE.match(s))


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
    return HTMLResponse(content=_read_html("admin.html"), headers=_NO_CACHE_HEADERS)


def _worker_html() -> HTMLResponse:
    return HTMLResponse(content=_read_html("worker.html"), headers=_NO_CACHE_HEADERS)


def _login_html() -> HTMLResponse:
    return HTMLResponse(content=_read_html("login.html"), headers=_NO_CACHE_HEADERS)


def _super_html() -> HTMLResponse:
    return HTMLResponse(content=_read_html("super.html"), headers=_NO_CACHE_HEADERS)


def _distributor_html() -> HTMLResponse:
    return HTMLResponse(content=_read_html("distributor.html"), headers=_NO_CACHE_HEADERS)


def _inject_slug_ctx(html: str, dist_slug: str, cust_slug: str) -> str:
    """HTML 의 <head> 직후에 `window.__WB_SLUGS__` 를 주입.

    페이지 (login.html / admin.html / worker.html) 의 JS 가 이 변수를 읽고
    슬러그 기반 로그인 / 좌상단 표시를 활성화한다.
    """
    payload = {"dist": dist_slug, "cust": cust_slug}
    script = (
        '<script>window.__WB_SLUGS__ = '
        + json.dumps(payload, ensure_ascii=False)
        + ';</script>'
    )
    # <head> 직후에 주입 (i18n.js 보다 먼저 실행되어야 함)
    return html.replace("</head>", script + "</head>", 1)


def _html_with_slugs(name: str, dist_slug: str, cust_slug: str) -> HTMLResponse:
    raw = _read_html(name)
    return HTMLResponse(content=_inject_slug_ctx(raw, dist_slug, cust_slug), headers=_NO_CACHE_HEADERS)


# i18n JS は内容が変わるたびにブラウザが必ず再取得するよう no-cache を強制。
# これらのファイルは小さく(数十KB)、毎回取りに行っても帯域・速度への影響は無視できる。
_NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _admin_i18n_js() -> FileResponse:
    return FileResponse(
        _STATIC / "admin-i18n.js",
        media_type="application/javascript",
        headers=_NO_CACHE_HEADERS,
    )


@router.get("/static/admin-i18n.js")
async def admin_i18n_js() -> FileResponse:
    return _admin_i18n_js()


@router.get("/static/login-i18n.js")
async def login_i18n_js() -> FileResponse:
    """ログイン画面の多言語辞書（ブラウザが /static/... で読み込む）。"""
    return FileResponse(
        _STATIC / "login-i18n.js",
        media_type="application/javascript",
        headers=_NO_CACHE_HEADERS,
    )


@router.get("/static/worker-i18n.js")
async def worker_i18n_js() -> FileResponse:
    """スタッフ画面の多言語辞書。"""
    return FileResponse(
        _STATIC / "worker-i18n.js",
        media_type="application/javascript",
        headers=_NO_CACHE_HEADERS,
    )


# アバター・指示画像は URL に ?t=<timestamp> が付くのでブラウザキャッシュは効くが、
# 同じ URL でファイル再書き込みされた場合に古いものを掴まないよう must-revalidate。
_AVATAR_CACHE_HEADERS = {
    "Cache-Control": "no-cache, must-revalidate",
}


@router.get("/static/uploads/staff-avatars/{account_id}.jpg")
async def staff_avatar_jpeg_file(account_id: str) -> FileResponse:
    """スタッフプロフィール画像（管理者アップロード）。"""
    try:
        uuid.UUID(account_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail="not found") from e
    path = _STATIC / "uploads" / "staff-avatars" / f"{account_id}.jpg"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path, media_type="image/jpeg", headers=_AVATAR_CACHE_HEADERS)


@router.get("/static/uploads/admin-avatars/{workspace_id}.jpg")
async def admin_avatar_jpeg_file(workspace_id: str) -> FileResponse:
    """管理者プロフィール画像（マイ情報でアップロード）。"""
    try:
        uuid.UUID(workspace_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail="not found") from e
    path = _STATIC / "uploads" / "admin-avatars" / f"{workspace_id}.jpg"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path, media_type="image/jpeg", headers=_AVATAR_CACHE_HEADERS)


@router.get("/static/uploads/workspace-logos/{workspace_id}.jpg")
async def workspace_logo_jpeg_file(workspace_id: str) -> FileResponse:
    """Phase 2.8: 워크스페이스 로고 (대리점이 산하 고객사 브랜딩에 업로드)."""
    try:
        uuid.UUID(workspace_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail="not found") from e
    path = _STATIC / "uploads" / "workspace-logos" / f"{workspace_id}.jpg"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path, media_type="image/jpeg", headers=_AVATAR_CACHE_HEADERS)


@router.get("/static/uploads/instruction-images/{workspace_id}/{filename}")
async def instruction_image_file(workspace_id: str, filename: str) -> FileResponse:
    """管理者が送信した指示用画像。"""
    try:
        uuid.UUID(workspace_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail="not found") from e
    if not re.match(r"^[0-9a-fA-F-]+\.(jpg|jpeg|png|webp|gif)$", filename, re.IGNORECASE):
        raise HTTPException(status_code=404, detail="not found")
    path = _STATIC / "uploads" / "instruction-images" / workspace_id / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    suf = filename.lower().rsplit(".", 1)[-1]
    mt = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
        "gif": "image/gif",
    }.get(suf, "application/octet-stream")
    return FileResponse(path, media_type=mt)


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


@router.get("/distributor")
async def distributor_page() -> HTMLResponse:
    """販売代理店ポータル (ログイン + 산하 워크스페이스 관리 SPA)."""
    return _distributor_html()


@router.get("/distributor/")
async def distributor_page_slash() -> HTMLResponse:
    return _distributor_html()


# ============================================================
# Phase 2.5 — URL 슬러그 라우팅 (3계층 멀티테넌시)
# ============================================================
# 라우트 매치 순서: 정적 라우트 (/admin, /login 등) → 동적 path-param
# 따라서 _RESERVED_SLUGS 의 슬러그는 정적 라우트가 우선 매치되므로 안전하다.
# 슬러그 형식 검증으로 잘못된 URL 은 명시적 404 를 반환.


@router.get("/{dist_slug}/{cust_slug}/admin")
async def slug_admin_page(dist_slug: str, cust_slug: str) -> HTMLResponse:
    """`/popo/abcramen/admin` → 점주 페이지 (admin.html + 슬러그 컨텍스트 주입)."""
    if not _is_valid_slug(dist_slug) or not _is_valid_slug(cust_slug):
        raise HTTPException(status_code=404, detail="Not Found")
    return _html_with_slugs("admin.html", dist_slug, cust_slug)


@router.get("/{dist_slug}/{cust_slug}/staff")
async def slug_staff_page(dist_slug: str, cust_slug: str) -> HTMLResponse:
    """`/popo/abcramen/staff` → 스탭 페이지 (worker.html + 슬러그 컨텍스트 주입)."""
    if not _is_valid_slug(dist_slug) or not _is_valid_slug(cust_slug):
        raise HTTPException(status_code=404, detail="Not Found")
    return _html_with_slugs("worker.html", dist_slug, cust_slug)


@router.get("/{dist_slug}/{cust_slug}")
async def slug_login_page(dist_slug: str, cust_slug: str) -> HTMLResponse:
    """`/popo/abcramen` → 로그인 페이지 (login.html + 슬러그 prefill).

    URL 만 알아도 점주·스탭 어느 쪽으로 진입할지 선택하면 슬러그가 자동으로
    `portal-login` 요청에 포함된다.
    """
    if not _is_valid_slug(dist_slug) or not _is_valid_slug(cust_slug):
        raise HTTPException(status_code=404, detail="Not Found")
    return _html_with_slugs("login.html", dist_slug, cust_slug)


@router.get("/{dist_slug}/{cust_slug}/admin/")
async def slug_admin_page_slash(dist_slug: str, cust_slug: str) -> HTMLResponse:
    return await slug_admin_page(dist_slug, cust_slug)


@router.get("/{dist_slug}/{cust_slug}/staff/")
async def slug_staff_page_slash(dist_slug: str, cust_slug: str) -> HTMLResponse:
    return await slug_staff_page(dist_slug, cust_slug)


@router.get("/{dist_slug}/{cust_slug}/")
async def slug_login_page_slash(dist_slug: str, cust_slug: str) -> HTMLResponse:
    return await slug_login_page(dist_slug, cust_slug)
