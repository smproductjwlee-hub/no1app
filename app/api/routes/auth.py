from __future__ import annotations

import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.services.staff_accounts import staff_accounts
from app.services.stores import (
    SUPER_WORKSPACE_ID,
    Role,
    join_tokens,
    sessions,
    worker_numbers,
    workspaces,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class JoinRequest(BaseModel):
    join_token: str = Field(..., min_length=8)
    user_label: Optional[str] = Field(None, max_length=100)


class SessionOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    workspace_id: str
    role: Role
    expires_in_seconds: int
    # 근로자 조인 시에만: 화면 표시용 (이름 또는 자동 No.n)
    worker_display_label: Optional[str] = None


def _normalize_worker_label(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    s = raw.strip()
    return s if s else None


def _exchange_join_token(
    raw_token: str,
    user_label: Optional[str],
    settings: Settings,
) -> SessionOut:
    jt = join_tokens.consume(raw_token)
    if jt is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired join token",
        )
    wid = jt.workspace_id
    label = _normalize_worker_label(user_label)
    if label is None:
        label = f"No.{worker_numbers.next(wid)}"
    sess = sessions.create(
        wid,
        Role.WORKER,
        user_label=label,
        ttl_seconds=settings.session_token_ttl_seconds,
    )
    return SessionOut(
        access_token=sess.token,
        workspace_id=sess.workspace_id,
        role=sess.role,
        expires_in_seconds=settings.session_token_ttl_seconds,
        worker_display_label=label,
    )


@router.post("/join", response_model=SessionOut)
async def join_with_token_post(
    body: JoinRequest,
    settings: Settings = Depends(get_settings),
) -> SessionOut:
    return _exchange_join_token(body.join_token, body.user_label, settings)


@router.get("/join", response_model=SessionOut)
async def join_with_token_get(
    token: str = Query(..., min_length=8),
    user_label: Optional[str] = Query(None, max_length=100),
    settings: Settings = Depends(get_settings),
) -> SessionOut:
    """QR 링크용: GET /api/v1/auth/join?token=... (조인 토큰 1회 소모)."""
    return _exchange_join_token(token, user_label, settings)


class PortalLoginRequest(BaseModel):
    role: Literal["admin", "worker", "super_admin"]
    username: str = Field("", max_length=200)
    password: str = Field(..., min_length=1, max_length=200)
    user_label: Optional[str] = Field(None, max_length=100)
    # スタッフ個人アカウント（設定時）: 共有PWではなく DB の個人PWで検証
    worker_account_login: Optional[str] = Field(None, max_length=100)


class PortalLoginOut(BaseModel):
    access_token: str
    role: Role
    workspace_id: Optional[str] = None
    workspace_name: Optional[str] = None
    worker_display_label: Optional[str] = None
    expires_in_seconds: int


class SuperAssumeIn(BaseModel):
    super_token: str = Field(..., min_length=8)
    workspace_id: str = Field(..., min_length=1)


class SuperAssumeOut(BaseModel):
    admin_token: str
    workspace_id: str
    workspace_name: str


def _resolve_workspace(username: str):
    raw = username.strip()
    if not raw:
        return None
    try:
        uuid.UUID(raw)
        return workspaces.get(raw)
    except ValueError:
        pass
    return workspaces.find_by_name(raw)


@router.post("/portal-login", response_model=PortalLoginOut)
async def portal_login(
    body: PortalLoginRequest,
    settings: Settings = Depends(get_settings),
) -> PortalLoginOut:
    """ログイン画面用: 管理者 / スタッフ / 総運営スーパー管理者。"""
    ttl = settings.session_token_ttl_seconds
    if body.role == "super_admin":
        if body.password != settings.super_admin_password:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            )
        sess = sessions.create(
            SUPER_WORKSPACE_ID,
            Role.SUPER_ADMIN,
            "super-admin",
            ttl_seconds=ttl,
        )
        return PortalLoginOut(
            access_token=sess.token,
            role=sess.role,
            workspace_id=None,
            workspace_name=None,
            expires_in_seconds=ttl,
        )

    if body.role == "admin":
        if not body.username.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="username (workspace name) required",
            )
        if body.password != settings.portal_admin_password:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            )
        name = body.username.strip() or "default"
        ws = workspaces.find_by_name(name) or workspaces.create(name)
        sess = sessions.create(ws.id, Role.ADMIN, "admin", ttl_seconds=ttl)
        return PortalLoginOut(
            access_token=sess.token,
            role=sess.role,
            workspace_id=ws.id,
            workspace_name=ws.name,
            expires_in_seconds=ttl,
        )

    if not body.username.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="username required",
        )
    ws = _resolve_workspace(body.username)
    if ws is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found",
        )
    acc_login = (body.worker_account_login or "").strip()
    if acc_login:
        acc = staff_accounts.get_by_workspace_login(ws.id, acc_login)
        if acc is None or not staff_accounts.verify_password(body.password, acc.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            )
        label = (acc.display_name or "").strip() or acc.login_id
        sess = sessions.create(
            ws.id,
            Role.WORKER,
            label,
            ttl_seconds=ttl,
            staff_account_id=acc.id,
        )
        return PortalLoginOut(
            access_token=sess.token,
            role=sess.role,
            workspace_id=ws.id,
            workspace_name=ws.name,
            worker_display_label=label,
            expires_in_seconds=ttl,
        )
    if body.password != settings.portal_worker_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    label = _normalize_worker_label(body.user_label)
    if label is None:
        label = f"No.{worker_numbers.next(ws.id)}"
    sess = sessions.create(ws.id, Role.WORKER, label, ttl_seconds=ttl)
    return PortalLoginOut(
        access_token=sess.token,
        role=sess.role,
        workspace_id=ws.id,
        workspace_name=ws.name,
        worker_display_label=label,
        expires_in_seconds=ttl,
    )


@router.get("/session")
async def session_info(token: str = Query(..., min_length=8)) -> dict:
    """Bearer 相当: クエリ token でセッション概要（QR 用 workspace_id など）。"""
    sess = sessions.get(token)
    if sess is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    if sess.role == Role.SUPER_ADMIN:
        return {
            "workspace_id": None,
            "workspace_name": None,
            "role": sess.role.value,
        }
    ws = workspaces.get(sess.workspace_id)
    return {
        "workspace_id": sess.workspace_id,
        "workspace_name": ws.name if ws else None,
        "role": sess.role.value,
    }


@router.post("/super-assume", response_model=SuperAssumeOut)
async def super_assume_workspace(
    body: SuperAssumeIn,
    settings: Settings = Depends(get_settings),
) -> SuperAssumeOut:
    """総運営のみ: 指定ワークスペースの管理者トークンを発行（顧客の管理者画面と同等）。"""
    s = sessions.get(body.super_token)
    if s is None or s.role != Role.SUPER_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="super admin only")
    ws = workspaces.get(body.workspace_id)
    if ws is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    admin_sess = sessions.create(
        ws.id,
        Role.ADMIN,
        "super-assumed",
        ttl_seconds=settings.session_token_ttl_seconds,
    )
    return SuperAssumeOut(
        admin_token=admin_sess.token,
        workspace_id=ws.id,
        workspace_name=ws.name,
    )
