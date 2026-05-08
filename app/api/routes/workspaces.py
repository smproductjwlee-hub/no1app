from __future__ import annotations

import uuid
from typing import Any, Optional

# 管理者 UI 言語（Chrome 等の Web Speech API で実用しやすいもののみ）
ALLOWED_ADMIN_UI_LOCALES = frozenset({"ja", "en", "ko", "zh", "vi", "id"})
from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, Field

from app.api.deps import run_db
from app.core.config import Settings, get_settings
from app.services.instruction_history import get_detail, list_rounds
from app.services.instruction_images import save_instruction_image_bytes
from app.services.staff_avatar_files import save_admin_square_jpeg, save_square_jpeg
from app.services.staff_accounts import PATCH_OMIT, staff_accounts
from app.services.staff_groups import staff_groups
from app.services.stores import Role, Workspace, sessions, workspaces
from app.services.ws_presence import ws_presence
from app.services.workspace_expression_terms import workspace_expression_terms
from app.services.workspace_glossary_terms import workspace_glossary_terms
from app.ws.manager import manager

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class WorkspaceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class WorkspaceReorderBody(BaseModel):
    ordered_workspace_ids: list[str] = Field(..., min_length=1)


class WorkspaceOut(BaseModel):
    id: str
    name: str
    admin_token: str


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
    admin_avatar_color_index: int = 0
    admin_avatar_url: Optional[str] = None


class WorkspaceOrgPatch(BaseModel):
    company_name: Optional[str] = Field(None, max_length=200)
    branch_name: Optional[str] = Field(None, max_length=200)
    department_name: Optional[str] = Field(None, max_length=200)
    admin_ui_locale: Optional[str] = Field(None, max_length=5)
    admin_avatar_color_index: Optional[int] = Field(None, ge=0, le=7)
    clear_admin_avatar: Optional[bool] = None


class StaffAccountOut(BaseModel):
    id: str
    workspace_id: str
    login_id: str
    display_name: str
    created_at: float
    group_id: Optional[str] = None
    profile_phone: str = ""
    profile_email: str = ""
    avatar_color_index: int = 0
    avatar_url: Optional[str] = None


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


class GlossaryTermCreate(BaseModel):
    sheet_gid: int = Field(..., description="food-glossary スプレッドシート内のシート gid（分野タブ）")
    word_ja: str = Field(..., min_length=1, max_length=500)
    meaning_ja: str = Field(..., min_length=1, max_length=2000)
    note_ja: str = Field("", max_length=4000)


class StaffAccountPatch(BaseModel):
    display_name: Optional[str] = Field(None, max_length=200)
    password: Optional[str] = Field(None, min_length=4, max_length=200)
    group_id: Optional[str] = Field(None, max_length=64)
    profile_phone: Optional[str] = Field(None, max_length=80)
    profile_email: Optional[str] = Field(None, max_length=200)
    avatar_color_index: Optional[int] = Field(None, ge=0, le=7)
    clear_avatar: Optional[bool] = None


def _require_admin_for_workspace(admin_token: str, workspace_id: str) -> None:
    sess = sessions.get(admin_token)
    if sess is None or sess.role != Role.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin only")
    if sess.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace mismatch")


def _require_admin_or_super(
    workspace_id: str,
    admin_token: Optional[str],
    super_token: Optional[str],
) -> None:
    if admin_token and super_token:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="use only one of admin_token or super_token",
        )
    if super_token:
        sess = sessions.get(super_token)
        if sess is None or sess.role != Role.SUPER_ADMIN:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="super admin only")
        if workspaces.get(workspace_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
        return
    if admin_token:
        _require_admin_for_workspace(admin_token, workspace_id)
        return
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="admin_token or super_token required",
    )


@router.get("", response_model=list[WorkspaceListRow])
async def list_all_workspaces(super_token: str = Query(..., description="総運営スーパー管理者トークン")) -> list[WorkspaceListRow]:
    """総運営のみ: 全ワークスペース一覧。"""
    sess = sessions.get(super_token)
    if sess is None or sess.role != Role.SUPER_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="super admin only")
    rows = await run_db(workspaces.list_all)
    return [
        WorkspaceListRow(
            id=w.id,
            name=w.name,
            company_name=w.company_name or "",
            branch_name=w.branch_name or "",
            department_name=w.department_name or "",
        )
        for w in rows
    ]


@router.post("/reorder", status_code=status.HTTP_204_NO_CONTENT)
async def reorder_workspaces_super(
    body: WorkspaceReorderBody,
    super_token: str = Query(..., description="総運営スーパー管理者トークン"),
) -> Response:
    sess = sessions.get(super_token)
    if sess is None or sess.role != Role.SUPER_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="super admin only")
    try:
        await run_db(workspaces.reorder_super, body.ordered_workspace_ids)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc) or "invalid reorder",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _workspace_org_out(ws: Workspace) -> WorkspaceOrgOut:
    loc = (ws.admin_ui_locale or "ja").strip()
    if loc not in ALLOWED_ADMIN_UI_LOCALES:
        loc = "ja"
    av_url: Optional[str] = None
    if ws.admin_avatar_updated_at:
        av_url = f"/static/uploads/admin-avatars/{ws.id}.jpg?t={int(ws.admin_avatar_updated_at)}"
    return WorkspaceOrgOut(
        workspace_id=ws.id,
        name=ws.name,
        company_name=ws.company_name or "",
        branch_name=ws.branch_name or "",
        department_name=ws.department_name or "",
        admin_ui_locale=loc,
        admin_avatar_color_index=int(ws.admin_avatar_color_index) % 8,
        admin_avatar_url=av_url,
    )


@router.get("/{workspace_id}/org", response_model=WorkspaceOrgOut)
async def get_workspace_org(
    workspace_id: str,
    admin_token: Optional[str] = Query(None, description="管理者セッショントークン"),
    super_token: Optional[str] = Query(None, description="総運営スーパー管理者トークン"),
) -> WorkspaceOrgOut:
    _require_admin_or_super(workspace_id, admin_token, super_token)
    ws = await run_db(workspaces.get, workspace_id)
    if ws is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return _workspace_org_out(ws)


@router.patch("/{workspace_id}/org", response_model=WorkspaceOrgOut)
async def patch_workspace_org(
    workspace_id: str,
    body: WorkspaceOrgPatch,
    admin_token: Optional[str] = Query(None, description="管理者セッショントークン"),
    super_token: Optional[str] = Query(None, description="総運営スーパー管理者トークン"),
) -> WorkspaceOrgOut:
    _require_admin_or_super(workspace_id, admin_token, super_token)
    d = body.model_dump(exclude_unset=True)
    if d.pop("clear_admin_avatar", None):
        await run_db(workspaces.clear_admin_avatar, workspace_id)
    if "admin_ui_locale" in d and d["admin_ui_locale"] is not None:
        al = str(d["admin_ui_locale"]).strip()
        if al and al not in ALLOWED_ADMIN_UI_LOCALES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="invalid admin_ui_locale",
            )
    org_kw: dict = {}
    for key in ("company_name", "branch_name", "department_name", "admin_ui_locale", "admin_avatar_color_index"):
        if key in d:
            org_kw[key] = d[key]
    ws: Optional[Workspace] = None
    if org_kw:
        ws = await run_db(workspaces.update_org, workspace_id, **org_kw)
    if ws is None:
        ws = await run_db(workspaces.get, workspace_id)
    if ws is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return _workspace_org_out(ws)


@router.post(
    "/{workspace_id}/admin-avatar",
    response_model=WorkspaceOrgOut,
)
async def upload_admin_avatar(
    workspace_id: str,
    admin_token: Optional[str] = Query(None, description="管理者セッショントークン"),
    super_token: Optional[str] = Query(None, description="総運営スーパー管理者トークン"),
    file: UploadFile = File(...),
) -> WorkspaceOrgOut:
    _require_admin_or_super(workspace_id, admin_token, super_token)
    if await run_db(workspaces.get, workspace_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="empty file")
    try:
        ts = await run_db(save_admin_square_jpeg, workspace_id, content)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    await run_db(workspaces.set_admin_avatar_updated_at, workspace_id, ts)
    ws = await run_db(workspaces.get, workspace_id)
    if ws is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return _workspace_org_out(ws)


def _staff_row(a) -> StaffAccountOut:
    av_url = None
    if a.avatar_updated_at:
        av_url = f"/static/uploads/staff-avatars/{a.id}.jpg?t={int(a.avatar_updated_at)}"
    return StaffAccountOut(
        id=a.id,
        workspace_id=a.workspace_id,
        login_id=a.login_id,
        display_name=a.display_name or "",
        created_at=a.created_at,
        group_id=a.group_id,
        profile_phone=a.profile_phone or "",
        profile_email=a.profile_email or "",
        avatar_color_index=int(a.avatar_color_index) % 8,
        avatar_url=av_url,
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
    admin_token: Optional[str] = Query(None, description="管理者セッショントークン"),
    super_token: Optional[str] = Query(None, description="総運営スーパー管理者トークン"),
) -> list[StaffGroupOut]:
    _require_admin_or_super(workspace_id, admin_token, super_token)
    rows = await run_db(staff_groups.list_for_workspace, workspace_id)
    return [_group_row(g) for g in rows]


@router.post(
    "/{workspace_id}/staff-groups",
    response_model=StaffGroupOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_staff_group(
    workspace_id: str,
    body: StaffGroupCreate,
    admin_token: Optional[str] = Query(None, description="管理者セッショントークン"),
    super_token: Optional[str] = Query(None, description="総運営スーパー管理者トークン"),
) -> StaffGroupOut:
    _require_admin_or_super(workspace_id, admin_token, super_token)
    try:
        g = await run_db(staff_groups.create, workspace_id, body.name)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    return _group_row(g)


@router.patch("/{workspace_id}/staff-groups/{group_id}", response_model=StaffGroupOut)
async def patch_staff_group(
    workspace_id: str,
    group_id: str,
    body: StaffGroupPatch,
    admin_token: Optional[str] = Query(None, description="管理者セッショントークン"),
    super_token: Optional[str] = Query(None, description="総運営スーパー管理者トークン"),
) -> StaffGroupOut:
    _require_admin_or_super(workspace_id, admin_token, super_token)
    try:
        g = await run_db(staff_groups.rename, group_id, workspace_id, body.name)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    if g is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return _group_row(g)


@router.delete("/{workspace_id}/staff-groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_staff_group(
    workspace_id: str,
    group_id: str,
    admin_token: Optional[str] = Query(None, description="管理者セッショントークン"),
    super_token: Optional[str] = Query(None, description="総運営スーパー管理者トークン"),
) -> Response:
    _require_admin_or_super(workspace_id, admin_token, super_token)
    if not await run_db(staff_groups.delete, group_id, workspace_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{workspace_id}/staff-accounts", response_model=list[StaffAccountOut])
async def list_staff_accounts(
    workspace_id: str,
    admin_token: Optional[str] = Query(None, description="管理者セッショントークン"),
    super_token: Optional[str] = Query(None, description="総運営スーパー管理者トークン"),
) -> list[StaffAccountOut]:
    _require_admin_or_super(workspace_id, admin_token, super_token)
    rows = await run_db(staff_accounts.list_for_workspace, workspace_id)
    return [_staff_row(a) for a in rows]


@router.post(
    "/{workspace_id}/staff-accounts",
    response_model=StaffAccountOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_staff_account(
    workspace_id: str,
    body: StaffAccountCreate,
    admin_token: Optional[str] = Query(None, description="管理者セッショントークン"),
    super_token: Optional[str] = Query(None, description="総運営スーパー管理者トークン"),
) -> StaffAccountOut:
    _require_admin_or_super(workspace_id, admin_token, super_token)
    if body.group_id and await run_db(staff_groups.get, body.group_id, workspace_id) is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid group_id")
    try:
        a = await run_db(
            staff_accounts.create,
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
    admin_token: Optional[str] = Query(None, description="管理者セッショントークン"),
    super_token: Optional[str] = Query(None, description="総運営スーパー管理者トークン"),
) -> StaffAccountOut:
    _require_admin_or_super(workspace_id, admin_token, super_token)
    try:
        uuid.UUID(account_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    d = body.model_dump(exclude_unset=True)
    if d.get("clear_avatar"):
        await run_db(staff_accounts.clear_avatar_image, account_id, workspace_id)
    d.pop("clear_avatar", None)
    gid_kw = PATCH_OMIT
    if "group_id" in d:
        raw_g = d["group_id"]
        gid_kw = (str(raw_g).strip() or None) if raw_g is not None else None
        if gid_kw and await run_db(staff_groups.get, gid_kw, workspace_id) is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid group_id")
    a = await run_db(
        staff_accounts.update,
        account_id,
        workspace_id,
        display_name=d["display_name"] if "display_name" in d else PATCH_OMIT,
        plain_password=d["password"] if "password" in d else PATCH_OMIT,
        group_id=gid_kw,
        profile_phone=d["profile_phone"] if "profile_phone" in d else PATCH_OMIT,
        profile_email=d["profile_email"] if "profile_email" in d else PATCH_OMIT,
        avatar_color_index=d["avatar_color_index"] if "avatar_color_index" in d else PATCH_OMIT,
    )
    if a is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return _staff_row(a)


@router.post(
    "/{workspace_id}/staff-accounts/{account_id}/avatar",
    response_model=StaffAccountOut,
)
async def upload_staff_avatar(
    workspace_id: str,
    account_id: str,
    admin_token: Optional[str] = Query(None, description="管理者セッショントークン"),
    super_token: Optional[str] = Query(None, description="総運営スーパー管理者トークン"),
    file: UploadFile = File(...),
) -> StaffAccountOut:
    _require_admin_or_super(workspace_id, admin_token, super_token)
    try:
        uuid.UUID(account_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    acc = await run_db(staff_accounts.get, account_id)
    if acc is None or acc.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="empty file")
    try:
        ts = await run_db(save_square_jpeg, account_id, content)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    a = await run_db(staff_accounts.update, account_id, workspace_id, avatar_updated_at=ts)
    if a is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return _staff_row(a)


@router.delete("/{workspace_id}/staff-accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_staff_account(
    workspace_id: str,
    account_id: str,
    admin_token: Optional[str] = Query(None, description="管理者セッショントークン"),
    super_token: Optional[str] = Query(None, description="総運営スーパー管理者トークン"),
) -> Response:
    _require_admin_or_super(workspace_id, admin_token, super_token)
    if not await run_db(staff_accounts.delete, account_id, workspace_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{workspace_id}/instruction-image")
async def upload_instruction_image(
    workspace_id: str,
    admin_token: str = Query(..., min_length=8),
    file: UploadFile = File(...),
) -> dict[str, str]:
    """指示用の画像を 1 枚アップロード（本文は WebSocket instruction の image_url に付与）。"""
    _require_admin_for_workspace(admin_token, workspace_id)
    raw = await file.read()
    url = await run_db(save_instruction_image_bytes, workspace_id, raw, file.content_type or "application/octet-stream")
    return {"url": url}


@router.get("/{workspace_id}/instruction-history")
async def instruction_history_list(
    workspace_id: str,
    admin_token: Optional[str] = Query(None, description="管理者セッショントークン"),
    super_token: Optional[str] = Query(None, description="総運営スーパー管理者トークン"),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict]:
    _require_admin_or_super(workspace_id, admin_token, super_token)
    return await run_db(list_rounds, workspace_id, limit=limit)


@router.get("/{workspace_id}/instruction-history/{instruction_id}")
async def instruction_history_detail(
    workspace_id: str,
    instruction_id: str,
    admin_token: Optional[str] = Query(None, description="管理者セッショントークン"),
    super_token: Optional[str] = Query(None, description="総運営スーパー管理者トークン"),
) -> dict:
    _require_admin_or_super(workspace_id, admin_token, super_token)
    d = await run_db(get_detail, workspace_id, instruction_id)
    if d is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Instruction not found")
    return d


@router.get("/online-workers", response_model=OnlineWorkersOut)
async def list_online_workers(
    admin_token: Optional[str] = Query(None),
    super_token: Optional[str] = Query(None),
    workspace_id: Optional[str] = Query(
        None,
        description="super_token 利用時は対象ワークスペース ID を指定",
    ),
) -> OnlineWorkersOut:
    """現在 WS 接続中のワーカーセッション一覧（管理者、または総運営＋workspace_id）。"""
    wid: Optional[str] = None
    if super_token:
        s = sessions.get(super_token)
        if s is None or s.role != Role.SUPER_ADMIN:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="super admin only")
        if not workspace_id or not workspace_id.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="workspace_id required with super_token",
            )
        wid = workspace_id.strip()
        if await run_db(workspaces.get, wid) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    elif admin_token:
        sess = sessions.get(admin_token)
        if sess is None or sess.role != Role.ADMIN:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin only")
        wid = sess.workspace_id
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="admin_token or super_token required",
        )
    workspace_id = wid
    await run_db(ws_presence.cleanup_stale, stale_seconds=120)
    pres = await run_db(ws_presence.list_online_workers, workspace_id)
    return OnlineWorkersOut(
        workers=[
            OnlineWorkerRow(token=p.token, label=p.label, staff_account_id=p.staff_account_id)
            for p in pres
        ]
    )


@router.post("", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: WorkspaceCreate,
    settings: Settings = Depends(get_settings),
) -> WorkspaceOut:
    ws = await run_db(workspaces.create, body.name)
    admin_sess = sessions.create(
        ws.id,
        Role.ADMIN,
        user_label="admin",
        ttl_seconds=settings.session_token_ttl_seconds,
    )
    return WorkspaceOut(id=ws.id, name=ws.name, admin_token=admin_sess.token)


@router.post("/{workspace_id}/glossary-terms", status_code=status.HTTP_201_CREATED)
async def add_workspace_glossary_term(
    workspace_id: str,
    body: GlossaryTermCreate,
    admin_token: str = Query(..., min_length=8),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """管理者が分野（シート）を選び、日本語の単語・意味・説明を登録。シート既存語と重複なら 409。"""
    _require_admin_for_workspace(admin_token, workspace_id)
    try:
        item = await run_db(
            workspace_glossary_terms.add,
            workspace_id,
            body.sheet_gid,
            body.word_ja,
            body.meaning_ja,
            body.note_ja,
            settings,
        )
        return {"ok": True, "item": item}
    except ValueError as exc:
        code = str(exc)
        if code in ("duplicate_sheet", "duplicate_workspace", "duplicate_expression_workspace"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=code) from exc
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=code) from exc


@router.post("/{workspace_id}/expression-terms", status_code=status.HTTP_201_CREATED)
async def add_workspace_expression_term(
    workspace_id: str,
    body: GlossaryTermCreate,
    admin_token: str = Query(..., min_length=8),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """管理者が分野（用語シートのタブ）を選び、現場専用の「表現」を SQLite に保存（Google シートは変更しない）。"""
    _require_admin_for_workspace(admin_token, workspace_id)
    try:
        item = await run_db(
            workspace_expression_terms.add,
            workspace_id,
            body.sheet_gid,
            body.word_ja,
            body.meaning_ja,
            body.note_ja,
            settings,
        )
        return {"ok": True, "item": item}
    except ValueError as exc:
        code = str(exc)
        if code in ("duplicate_sheet", "duplicate_workspace", "duplicate_glossary_workspace"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=code) from exc
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=code) from exc
