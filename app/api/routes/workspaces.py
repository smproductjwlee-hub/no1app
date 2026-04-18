from __future__ import annotations

import io
from typing import Optional

import qrcode

# 管理者 UI 言語（Chrome 等の Web Speech API で実用しやすいもののみ）
ALLOWED_ADMIN_UI_LOCALES = frozenset({"ja", "en", "ko", "zh", "vi", "id"})
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.services.stores import Role, join_tokens, sessions, workspaces
from app.ws.manager import manager

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class WorkspaceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class WorkspaceOut(BaseModel):
    id: str
    name: str
    admin_token: str


class JoinInfoOut(BaseModel):
    workspace_id: str
    join_url: str
    join_token: str
    expires_in_seconds: int


class OnlineWorkerRow(BaseModel):
    token: str
    label: str


class OnlineWorkersOut(BaseModel):
    workers: list[OnlineWorkerRow]


class WorkspaceListRow(BaseModel):
    id: str
    name: str
    company_name: str = ""
    branch_name: str = ""
    department_name: str = ""


class WorkspaceOrgOut(BaseModel):
    workspace_id: str
    name: str
    company_name: str
    branch_name: str
    department_name: str
    admin_ui_locale: str = "ja"


class WorkspaceOrgPatch(BaseModel):
    company_name: Optional[str] = Field(None, max_length=200)
    branch_name: Optional[str] = Field(None, max_length=200)
    department_name: Optional[str] = Field(None, max_length=200)
    admin_ui_locale: Optional[str] = Field(None, max_length=5)


def _require_admin_for_workspace(admin_token: str, workspace_id: str) -> None:
    sess = sessions.get(admin_token)
    if sess is None or sess.role != Role.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin only")
    if sess.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace mismatch")


@router.get("", response_model=list[WorkspaceListRow])
async def list_all_workspaces(super_token: str = Query(..., description="総運営スーパー管理者トークン")) -> list[WorkspaceListRow]:
    """総運営のみ: 全ワークスペース一覧。"""
    sess = sessions.get(super_token)
    if sess is None or sess.role != Role.SUPER_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="super admin only")
    return [
        WorkspaceListRow(
            id=w.id,
            name=w.name,
            company_name=w.company_name or "",
            branch_name=w.branch_name or "",
            department_name=w.department_name or "",
        )
        for w in workspaces.list_all()
    ]


@router.get("/{workspace_id}/org", response_model=WorkspaceOrgOut)
async def get_workspace_org(
    workspace_id: str,
    admin_token: str = Query(..., description="管理者セッショントークン"),
) -> WorkspaceOrgOut:
    _require_admin_for_workspace(admin_token, workspace_id)
    ws = workspaces.get(workspace_id)
    if ws is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    loc = (ws.admin_ui_locale or "ja").strip()
    if loc not in ALLOWED_ADMIN_UI_LOCALES:
        loc = "ja"
    return WorkspaceOrgOut(
        workspace_id=ws.id,
        name=ws.name,
        company_name=ws.company_name or "",
        branch_name=ws.branch_name or "",
        department_name=ws.department_name or "",
        admin_ui_locale=loc,
    )


@router.patch("/{workspace_id}/org", response_model=WorkspaceOrgOut)
async def patch_workspace_org(
    workspace_id: str,
    body: WorkspaceOrgPatch,
    admin_token: str = Query(..., description="管理者セッショントークン"),
) -> WorkspaceOrgOut:
    _require_admin_for_workspace(admin_token, workspace_id)
    if body.admin_ui_locale is not None:
        al = body.admin_ui_locale.strip()
        if al and al not in ALLOWED_ADMIN_UI_LOCALES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="invalid admin_ui_locale",
            )
    ws = workspaces.update_org(
        workspace_id,
        company_name=body.company_name,
        branch_name=body.branch_name,
        department_name=body.department_name,
        admin_ui_locale=body.admin_ui_locale,
    )
    if ws is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    loc = (ws.admin_ui_locale or "ja").strip()
    if loc not in ALLOWED_ADMIN_UI_LOCALES:
        loc = "ja"
    return WorkspaceOrgOut(
        workspace_id=ws.id,
        name=ws.name,
        company_name=ws.company_name or "",
        branch_name=ws.branch_name or "",
        department_name=ws.department_name or "",
        admin_ui_locale=loc,
    )


@router.get("/online-workers", response_model=OnlineWorkersOut)
async def list_online_workers(admin_token: str = Query(...)) -> OnlineWorkersOut:
    """現在 WS 接続中のワーカーセッション一覧（管理者のみ）。"""
    sess = sessions.get(admin_token)
    if sess is None or sess.role != Role.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin only")
    workspace_id = sess.workspace_id
    rows: list[OnlineWorkerRow] = []
    seen: set[str] = set()
    for client_token, _ws in manager.iter_connections(workspace_id):
        if not client_token or client_token in seen:
            continue
        w = sessions.get(client_token)
        if w is None or w.role != Role.WORKER:
            continue
        seen.add(client_token)
        rows.append(
            OnlineWorkerRow(token=client_token, label=w.user_label or "スタッフ"),
        )
    return OnlineWorkersOut(workers=rows)


@router.post("", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: WorkspaceCreate,
    settings: Settings = Depends(get_settings),
) -> WorkspaceOut:
    ws = workspaces.create(body.name)
    admin_sess = sessions.create(
        ws.id,
        Role.ADMIN,
        user_label="admin",
        ttl_seconds=settings.session_token_ttl_seconds,
    )
    return WorkspaceOut(id=ws.id, name=ws.name, admin_token=admin_sess.token)


@router.get("/{workspace_id}/join-info", response_model=JoinInfoOut)
async def get_join_info(
    workspace_id: str,
    settings: Settings = Depends(get_settings),
) -> JoinInfoOut:
    ws = workspaces.get(workspace_id)
    if ws is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    jt = join_tokens.issue(workspace_id, settings.join_token_ttl_seconds)
    join_url = f"{settings.public_base_url.rstrip('/')}/worker?join_token={jt.token}"
    return JoinInfoOut(
        workspace_id=workspace_id,
        join_url=join_url,
        join_token=jt.token,
        expires_in_seconds=settings.join_token_ttl_seconds,
    )


@router.get("/{workspace_id}/qr.png")
async def workspace_join_qr_png(
    workspace_id: str,
    settings: Settings = Depends(get_settings),
) -> Response:
    ws = workspaces.get(workspace_id)
    if ws is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    jt = join_tokens.issue(workspace_id, settings.join_token_ttl_seconds)
    join_url = f"{settings.public_base_url.rstrip('/')}/worker?join_token={jt.token}"
    img = qrcode.make(join_url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")
