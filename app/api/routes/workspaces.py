from __future__ import annotations

import io
from typing import Optional

import qrcode

# 管理者 UI 言語（Chrome 等の Web Speech API で実用しやすいもののみ）
ALLOWED_ADMIN_UI_LOCALES = frozenset({"ja", "en", "ko", "zh", "vi", "id"})
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.services.instruction_history import get_detail, list_rounds
from app.services.staff_accounts import PATCH_OMIT, staff_accounts
from app.services.staff_groups import staff_groups
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
    staff_account_id: Optional[str] = None


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


class StaffAccountOut(BaseModel):
    id: str
    workspace_id: str
    login_id: str
    display_name: str
    created_at: float
    group_id: Optional[str] = None


class StaffAccountCreate(BaseModel):
    login_id: str = Field(..., min_length=1, max_length=100)
    display_name: str = Field("", max_length=200)
    password: str = Field(..., min_length=4, max_length=200)
    group_id: Optional[str] = Field(None, max_length=64)


class StaffGroupOut(BaseModel):
    id: str
    workspace_id: str
    name: str
    sort_order: float
    created_at: float


class StaffGroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class StaffGroupPatch(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class StaffAccountPatch(BaseModel):
    display_name: Optional[str] = Field(None, max_length=200)
    password: Optional[str] = Field(None, min_length=4, max_length=200)
    group_id: Optional[str] = Field(None, max_length=64)


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


def _staff_row(a) -> StaffAccountOut:
    return StaffAccountOut(
        id=a.id,
        workspace_id=a.workspace_id,
        login_id=a.login_id,
        display_name=a.display_name or "",
        created_at=a.created_at,
        group_id=a.group_id,
    )


def _group_row(g) -> StaffGroupOut:
    return StaffGroupOut(
        id=g.id,
        workspace_id=g.workspace_id,
        name=g.name,
        sort_order=g.sort_order,
        created_at=g.created_at,
    )


@router.get("/{workspace_id}/staff-groups", response_model=list[StaffGroupOut])
async def list_staff_groups(
    workspace_id: str,
    admin_token: str = Query(..., description="管理者セッショントークン"),
) -> list[StaffGroupOut]:
    _require_admin_for_workspace(admin_token, workspace_id)
    return [_group_row(g) for g in staff_groups.list_for_workspace(workspace_id)]


@router.post(
    "/{workspace_id}/staff-groups",
    response_model=StaffGroupOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_staff_group(
    workspace_id: str,
    body: StaffGroupCreate,
    admin_token: str = Query(..., description="管理者セッショントークン"),
) -> StaffGroupOut:
    _require_admin_for_workspace(admin_token, workspace_id)
    try:
        g = staff_groups.create(workspace_id, body.name)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    return _group_row(g)


@router.patch("/{workspace_id}/staff-groups/{group_id}", response_model=StaffGroupOut)
async def patch_staff_group(
    workspace_id: str,
    group_id: str,
    body: StaffGroupPatch,
    admin_token: str = Query(..., description="管理者セッショントークン"),
) -> StaffGroupOut:
    _require_admin_for_workspace(admin_token, workspace_id)
    try:
        g = staff_groups.rename(group_id, workspace_id, body.name)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    if g is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return _group_row(g)


@router.delete("/{workspace_id}/staff-groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_staff_group(
    workspace_id: str,
    group_id: str,
    admin_token: str = Query(..., description="管理者セッショントークン"),
) -> Response:
    _require_admin_for_workspace(admin_token, workspace_id)
    if not staff_groups.delete(group_id, workspace_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{workspace_id}/staff-accounts", response_model=list[StaffAccountOut])
async def list_staff_accounts(
    workspace_id: str,
    admin_token: str = Query(..., description="管理者セッショントークン"),
) -> list[StaffAccountOut]:
    _require_admin_for_workspace(admin_token, workspace_id)
    return [_staff_row(a) for a in staff_accounts.list_for_workspace(workspace_id)]


@router.post(
    "/{workspace_id}/staff-accounts",
    response_model=StaffAccountOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_staff_account(
    workspace_id: str,
    body: StaffAccountCreate,
    admin_token: str = Query(..., description="管理者セッショントークン"),
) -> StaffAccountOut:
    _require_admin_for_workspace(admin_token, workspace_id)
    if body.group_id and staff_groups.get(body.group_id, workspace_id) is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid group_id")
    try:
        a = staff_accounts.create(
            workspace_id,
            body.login_id,
            body.display_name,
            body.password,
            group_id=body.group_id,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e) or "conflict",
        ) from e
    return _staff_row(a)


@router.patch("/{workspace_id}/staff-accounts/{account_id}", response_model=StaffAccountOut)
async def patch_staff_account(
    workspace_id: str,
    account_id: str,
    body: StaffAccountPatch,
    admin_token: str = Query(..., description="管理者セッショントークン"),
) -> StaffAccountOut:
    _require_admin_for_workspace(admin_token, workspace_id)
    d = body.model_dump(exclude_unset=True)
    gid_kw = PATCH_OMIT
    if "group_id" in d:
        raw_g = d["group_id"]
        gid_kw = (str(raw_g).strip() or None) if raw_g is not None else None
        if gid_kw and staff_groups.get(gid_kw, workspace_id) is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid group_id")
    a = staff_accounts.update(
        account_id,
        workspace_id,
        display_name=d["display_name"] if "display_name" in d else PATCH_OMIT,
        plain_password=d["password"] if "password" in d else PATCH_OMIT,
        group_id=gid_kw,
    )
    if a is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return _staff_row(a)


@router.delete("/{workspace_id}/staff-accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_staff_account(
    workspace_id: str,
    account_id: str,
    admin_token: str = Query(..., description="管理者セッショントークン"),
) -> Response:
    _require_admin_for_workspace(admin_token, workspace_id)
    if not staff_accounts.delete(account_id, workspace_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{workspace_id}/instruction-history")
async def instruction_history_list(
    workspace_id: str,
    admin_token: str = Query(..., description="管理者セッショントークン"),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict]:
    _require_admin_for_workspace(admin_token, workspace_id)
    return list_rounds(workspace_id, limit=limit)


@router.get("/{workspace_id}/instruction-history/{instruction_id}")
async def instruction_history_detail(
    workspace_id: str,
    instruction_id: str,
    admin_token: str = Query(..., description="管理者セッショントークン"),
) -> dict:
    _require_admin_for_workspace(admin_token, workspace_id)
    d = get_detail(workspace_id, instruction_id)
    if d is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Instruction not found")
    return d


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
            OnlineWorkerRow(
                token=client_token,
                label=w.user_label or "スタッフ",
                staff_account_id=w.staff_account_id,
            ),
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
