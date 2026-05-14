from __future__ import annotations

import time
import uuid
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field

from app.services.workspace_chat import append as chat_append, list_recent as chat_list_recent

from app.api.deps import run_db
from app.core.config import Settings, get_settings
from app.services.instruction_history import (
    list_pending_instructions_for_worker,
    list_recent_eligible_instructions,
    list_worker_instruction_history,
    list_worker_instruction_history_ng_only,
    record_reply,
    worker_can_submit_reply,
)
from app.services.distributors import distributors as distributors_store, verify_password as verify_distributor_password
from app.services.staff_avatar_files import save_square_jpeg
from app.services.worker_glossary_saves import worker_glossary_saves
from app.services.workspace_glossary_terms import workspace_glossary_terms
from app.services.staff_accounts import staff_accounts
from app.services.stores import (
    SUPER_WORKSPACE_ID,
    Role,
    sessions,
    workspaces,
)
from app.ws.manager import manager

router = APIRouter(prefix="/auth", tags=["auth"])


class PortalLoginRequest(BaseModel):
    role: Literal["admin", "worker", "super_admin", "distributor_admin"]
    username: str = Field("", max_length=200)
    password: str = Field(..., min_length=1, max_length=200)
    # 現場スタッフは個人アカウント必須（共有パスワード方式は廃止）
    worker_account_login: Optional[str] = Field(None, max_length=100)
    # Phase 2.3 — URL 슬러그 기반 로그인 (3계층 라우팅)
    # admin: distributor_slug + workspace_slug 로 워크스페이스 식별
    # worker: 동일 + worker_account_login
    # distributor_admin: distributor_slug 만 (또는 username = owner_email)
    distributor_slug: Optional[str] = Field(None, max_length=20)
    workspace_slug: Optional[str] = Field(None, max_length=20)


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


def _resolve_by_login_input(body: PortalLoginRequest):
    """Phase 2.3 통합 워크스페이스 해석.

    우선순위:
      1. distributor_slug + workspace_slug (3계층 URL 라우팅 — 권장)
      2. username (UUID / 워크스페이스명 — legacy, c-direct 스코프)
    """
    ds = (body.distributor_slug or "").strip().lower()
    ws_slug = (body.workspace_slug or "").strip().lower()
    if ds and ws_slug:
        return workspaces.find_by_slugs(ds, ws_slug)
    return _resolve_workspace(body.username)


def _assert_workspace_login_allowed(ws) -> None:
    """Phase 2.14 (A4): 워크스페이스 점장·스탭 로그인 허용 여부 검증.

    대리점 (distributor) 이 suspended 상태이면 산하 모든 워크스페이스 로그인 차단.
    c-direct (직판) 산하는 검증 생략.
    """
    if ws is None or not ws.distributor_id:
        return
    d = distributors_store.get(ws.distributor_id)
    if d is None:
        return
    if d.slug == "c-direct":
        return
    if d.status == "suspended":
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"This workspace is temporarily suspended. Please contact {d.name} ({d.contact_email or d.contact_phone or 'your distributor'}) for assistance.",
        )


@router.post("/portal-login", response_model=PortalLoginOut)
async def portal_login(
    body: PortalLoginRequest,
    settings: Settings = Depends(get_settings),
) -> PortalLoginOut:
    """ログイン画面用: 管理者 / スタッフ / 総運営スーパー管理者 / 大리점관리자。"""
    ttl = settings.session_token_ttl_seconds

    # ============================================================
    # 1. Super Admin (변경 없음)
    # ============================================================
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

    # ============================================================
    # 2. Distributor Admin (신규 — Phase 2.3)
    # ============================================================
    if body.role == "distributor_admin":
        # username = owner_email (또는 distributor_slug 도 받아들임)
        login_id = body.username.strip()
        if not login_id and body.distributor_slug:
            # 슬러그로도 식별 가능 (간단 테스트용)
            d = await run_db(distributors_store.get_by_slug, body.distributor_slug.strip().lower())
        else:
            d = await run_db(distributors_store.get_by_owner_email, login_id)
        if d is None or not d.is_active():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            )
        if not verify_distributor_password(body.password, d.owner_password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            )
        # c-direct 는 가상 대리점이므로 로그인 불가 (super_admin 만 관리)
        if d.slug == "c-direct":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="The c-direct distributor cannot be used for login",
            )
        sess = sessions.create(
            workspace_id="",  # 대리점 관리자는 워크스페이스 미보유
            role=Role.DISTRIBUTOR_ADMIN,
            user_label=(d.contact_person or d.name),
            ttl_seconds=ttl,
            distributor_id=d.id,
        )
        return PortalLoginOut(
            access_token=sess.token,
            role=sess.role,
            workspace_id=None,
            workspace_name=d.name,
            expires_in_seconds=ttl,
        )

    # ============================================================
    # 3. Workspace Admin (점주) — 슬러그 우선 + 기존 PW fallback
    # ============================================================
    if body.role == "admin":
        ws = await run_db(_resolve_by_login_input, body)
        # 슬러그 기반 로그인: 워크스페이스의 owner_password_hash 와 검증
        ds = (body.distributor_slug or "").strip().lower()
        ws_slug = (body.workspace_slug or "").strip().lower()
        if ds and ws_slug:
            if ws is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Workspace not found",
                )
            # Phase 2.14 (A4): 대리점 일시정지 검증
            await run_db(_assert_workspace_login_allowed, ws)
            # 워크스페이스 owner_password_hash 가 있으면 그걸로 검증
            if ws.owner_password_hash:
                if not verify_distributor_password(body.password, ws.owner_password_hash):
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid credentials",
                    )
            else:
                # legacy: PW 미설정 워크스페이스는 글로벌 PW 로 진입 가능 (테스트 호환)
                if body.password != settings.portal_admin_password:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid credentials",
                    )
            sess = sessions.create(
                ws.id, Role.ADMIN, "admin",
                ttl_seconds=ttl,
                distributor_id=ws.distributor_id,
            )
            return PortalLoginOut(
                access_token=sess.token,
                role=sess.role,
                workspace_id=ws.id,
                workspace_name=ws.name,
                expires_in_seconds=ttl,
            )

        # Legacy 경로: username (워크스페이스명) + 글로벌 portal_admin_password
        if not body.username.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="username (workspace name) or slug required",
            )
        if body.password != settings.portal_admin_password:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            )
        name = body.username.strip() or "default"
        if ws is None:
            ws = await run_db(workspaces.create, name)
        sess = sessions.create(
            ws.id, Role.ADMIN, "admin",
            ttl_seconds=ttl,
            distributor_id=ws.distributor_id,
        )
        return PortalLoginOut(
            access_token=sess.token,
            role=sess.role,
            workspace_id=ws.id,
            workspace_name=ws.name,
            expires_in_seconds=ttl,
        )

    # ============================================================
    # 4. Worker (스탭) — 슬러그 또는 username 으로 워크스페이스 식별
    # ============================================================
    ws = await run_db(_resolve_by_login_input, body)
    if ws is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found",
        )
    # Phase 2.14 (A4): 대리점 일시정지 검증
    await run_db(_assert_workspace_login_allowed, ws)
    acc_login = (body.worker_account_login or "").strip()
    if not acc_login:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="staff login id required",
        )
    acc = await run_db(staff_accounts.get_by_workspace_login, ws.id, acc_login)
    if acc is None or not await run_db(staff_accounts.verify_password, body.password, acc.password_hash):
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
        distributor_id=ws.distributor_id,
    )
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
    """Bearer 相当: クエリ token でセッション概要。

    Phase 2.3 — admin/worker 응답에 좌상단 표시용 메타 (distributor 연락처·
    워크스페이스 로고) 를 동봉.
    """
    sess = sessions.get(token)
    if sess is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")

    # Super Admin
    if sess.role == Role.SUPER_ADMIN:
        return {
            "workspace_id": None,
            "workspace_name": None,
            "role": sess.role.value,
        }

    # Distributor Admin (워크스페이스 미보유)
    if sess.role == Role.DISTRIBUTOR_ADMIN:
        d = await run_db(distributors_store.get, sess.distributor_id) if sess.distributor_id else None
        return {
            "workspace_id": None,
            "workspace_name": d.name if d else None,
            "role": sess.role.value,
            "distributor_id": sess.distributor_id or None,
            "distributor_slug": d.slug if d else None,
            "distributor_name": d.name if d else None,
        }

    # Admin / Worker
    ws = await run_db(workspaces.get, sess.workspace_id)
    out: dict = {
        "workspace_id": sess.workspace_id,
        "workspace_name": ws.name if ws else None,
        "role": sess.role.value,
    }
    if ws is not None:
        out["workspace_slug"] = ws.slug or None
        out["logo_url"] = ws.logo_url or None
        out["primary_color"] = ws.primary_color or None
        out["assigned_plan"] = ws.assigned_plan or "starter"
        # 좌상단 「お困りの際は」 표시용 대리점 연락처
        if ws.distributor_id:
            d = await run_db(distributors_store.get, ws.distributor_id)
            if d is not None and d.slug != "c-direct":
                out["distributor"] = {
                    "id": d.id,
                    "slug": d.slug,
                    "name": d.name,
                    "contact_person": d.contact_person,
                    "contact_phone": d.contact_phone,
                    "contact_email": d.contact_email,
                }
            else:
                # c-direct (직판) 는 대리점 정보를 노출하지 않음
                out["distributor"] = None
    if sess.role == Role.WORKER:
        out["worker_display_label"] = sess.user_label or ""
        out["has_staff_account"] = bool(sess.staff_account_id)
    return out


def _require_worker_session(token: str):
    sess = sessions.get(token)
    if sess is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    if sess.role != Role.WORKER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="worker only")
    return sess


@router.get("/worker-profile")
async def worker_profile(token: str = Query(..., min_length=8)) -> dict[str, Any]:
    """スタッフ本人向け：表示名・顔写真URL・個人アカウント有無。"""
    sess = _require_worker_session(token)
    wid = sess.workspace_id
    label = (sess.user_label or "").strip() or "?"
    out: dict[str, Any] = {
        "worker_display_label": label,
        "has_staff_account": bool(sess.staff_account_id),
    }
    if not sess.staff_account_id:
        return out
    acc = await run_db(staff_accounts.get, sess.staff_account_id)
    if acc is None or acc.workspace_id != wid:
        return out
    out["login_id"] = acc.login_id
    out["display_name"] = acc.display_name or ""
    out["avatar_color_index"] = int(acc.avatar_color_index or 0) % 8
    if acc.avatar_updated_at:
        out["avatar_url"] = f"/static/uploads/staff-avatars/{acc.id}.jpg?t={int(acc.avatar_updated_at)}"
    else:
        out["avatar_url"] = None
    return out


class WorkerPasswordChangeIn(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=200)
    new_password: str = Field(..., min_length=4, max_length=200)


@router.post("/worker-password")
async def worker_change_password(
    body: WorkerPasswordChangeIn,
    token: str = Query(..., min_length=8),
) -> dict[str, bool]:
    sess = _require_worker_session(token)
    if not sess.staff_account_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="staff account required")
    acc = await run_db(staff_accounts.get, sess.staff_account_id)
    if acc is None or acc.workspace_id != sess.workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    if not await run_db(staff_accounts.verify_password, body.current_password, acc.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid current password")
    await run_db(staff_accounts.update, acc.id, sess.workspace_id, plain_password=body.new_password)
    return {"ok": True}


# Phase 2.12 (A2): 점장 본인 PW 변경 — 슬러그 모드 워크스페이스에 한정
class AdminPasswordChangeIn(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=200)
    new_password: str = Field(..., min_length=4, max_length=200)


@router.post("/admin-password")
async def admin_change_password(
    body: AdminPasswordChangeIn,
    token: str = Query(..., min_length=8),
) -> dict[str, bool]:
    """点장 본인이 자기 워크스페이스 PW 를 변경.

    조건:
    - admin role 세션
    - 워크스페이스의 owner_password_hash 가 설정되어 있어야 함 (슬러그 모드)
    - legacy (글로벌 PORTAL_ADMIN_PASSWORD) 모드는 거부 → 대리점이 먼저
      슬러그 모드로 전환해야 함 (PW 재발급 통해)
    """
    from app.services.distributors import (
        hash_password as _hash_pw,
        verify_password as _verify_pw,
    )

    sess = sessions.get(token)
    if sess is None or sess.role != Role.ADMIN:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="admin only")
    ws = await run_db(workspaces.get, sess.workspace_id)
    if ws is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workspace not found")
    if not ws.owner_password_hash:
        # legacy: 글로벌 PW 사용 중. 점장이 직접 변경 불가.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="このワークスペースはまだ個別パスワードが設定されていません。代理店または運営事務局にお問い合わせください。",
        )
    if not _verify_pw(body.current_password, ws.owner_password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="現在のパスワードが正しくありません",
        )
    new_hash = _hash_pw(body.new_password)
    await run_db(workspaces.set_owner_password_hash, sess.workspace_id, new_hash)
    return {"ok": True}


@router.post("/worker-avatar")
async def worker_upload_own_avatar(
    token: str = Query(..., min_length=8),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    sess = _require_worker_session(token)
    if not sess.staff_account_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="staff account required")
    acc = await run_db(staff_accounts.get, sess.staff_account_id)
    if acc is None or acc.workspace_id != sess.workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="empty file")
    try:
        ts = await run_db(save_square_jpeg, acc.id, content)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    a = await run_db(staff_accounts.update, acc.id, sess.workspace_id, avatar_updated_at=ts)
    if a is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    return {
        "ok": True,
        "avatar_url": f"/static/uploads/staff-avatars/{a.id}.jpg?t={int(ts)}",
    }


@router.delete("/worker-avatar")
async def worker_delete_own_avatar(token: str = Query(..., min_length=8)) -> dict[str, bool]:
    sess = _require_worker_session(token)
    if not sess.staff_account_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="staff account required")
    acc = await run_db(staff_accounts.get, sess.staff_account_id)
    if acc is None or acc.workspace_id != sess.workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    await run_db(staff_accounts.clear_avatar_image, acc.id, sess.workspace_id)
    return {"ok": True}


@router.get("/worker-ng-replies")
async def worker_ng_replies_only(
    token: str = Query(..., min_length=8),
    limit: int = Query(80, ge=1, le=200),
) -> list[dict]:
    sess = _require_worker_session(token)
    return await run_db(
        list_worker_instruction_history_ng_only,
        sess.workspace_id, sess.token, limit=limit, staff_account_id=sess.staff_account_id,
    )


class WorkerGlossarySaveIn(BaseModel):
    kind: Literal["word", "expression"]
    sheet_gid: int = 0
    item: dict[str, str] = Field(default_factory=dict)


@router.get("/worker-glossary-saves")
async def worker_list_glossary_saves(
    token: str = Query(..., min_length=8),
    kind: Literal["word", "expression"] = Query(...),
    limit: int = Query(200, ge=1, le=500),
) -> list[dict]:
    sess = _require_worker_session(token)
    if not sess.staff_account_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="staff account required")
    return await run_db(worker_glossary_saves.list, sess.workspace_id, sess.staff_account_id, kind, limit=limit)


@router.post("/worker-glossary-saves")
async def worker_add_glossary_save(
    body: WorkerGlossarySaveIn,
    token: str = Query(..., min_length=8),
) -> dict[str, Any]:
    sess = _require_worker_session(token)
    if not sess.staff_account_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="staff account required")
    row = await run_db(
        worker_glossary_saves.add,
        sess.workspace_id,
        sess.staff_account_id,
        body.kind,
        body.sheet_gid,
        body.item,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="could not save")
    return row


@router.delete("/worker-glossary-saves/{save_id}")
async def worker_remove_glossary_save(
    save_id: str,
    token: str = Query(..., min_length=8),
) -> dict[str, bool]:
    sess = _require_worker_session(token)
    if not sess.staff_account_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="staff account required")
    ok = await run_db(worker_glossary_saves.delete, sess.workspace_id, sess.staff_account_id, save_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return {"ok": True}


@router.get("/worker-food-glossary")
async def worker_food_glossary_merged(
    token: str = Query(..., min_length=8),
    sheet_gid: Optional[int] = Query(
        None,
        description="用語シートの gid（未指定時はサーバー既定の food_glossary_sheet_gid）",
    ),
    settings: Settings = Depends(get_settings),
) -> Any:
    """Google シートの用語＋当ワークスペース管理者が追加した用語を結合して返す。"""
    sess = _require_worker_session(token)
    return await run_db(workspace_glossary_terms.merged_food_glossary, sess.workspace_id, settings, sheet_gid)


@router.get("/worker-instruction-history")
async def worker_instruction_history(
    token: str = Query(..., min_length=8),
    limit: int = Query(80, ge=1, le=200),
) -> list[dict]:
    """スタッフ本人が返信した指示の履歴（新しい順）。"""
    sess = sessions.get(token)
    if sess is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    if sess.role != Role.WORKER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="worker only")
    return await run_db(
        list_worker_instruction_history,
        sess.workspace_id, sess.token, limit=limit, staff_account_id=sess.staff_account_id,
    )


@router.get("/worker-pending-instructions")
async def worker_pending_instructions(
    token: str = Query(..., min_length=8),
    limit: int = Query(40, ge=1, le=100),
) -> list[dict]:
    """未返信の指示（遅れてログインした場合など）。古い順。"""
    sess = sessions.get(token)
    if sess is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    if sess.role != Role.WORKER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="worker only")
    return await run_db(
        list_pending_instructions_for_worker,
        sess.workspace_id,
        sess.token,
        sess.staff_account_id,
        limit=limit,
    )


@router.get("/worker-recent-instructions")
async def worker_recent_instructions(
    token: str = Query(..., min_length=8),
    limit: int = Query(10, ge=1, le=50),
) -> list[dict]:
    """自分が対象の指示を新しい順（返信済み含む）。未返信が無いときの最新表示用。"""
    sess = sessions.get(token)
    if sess is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    if sess.role != Role.WORKER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="worker only")
    return await run_db(
        list_recent_eligible_instructions,
        sess.workspace_id,
        sess.token,
        sess.staff_account_id,
        limit=limit,
    )


class WorkerInstructionReplyIn(BaseModel):
    instruction_id: str = Field(..., min_length=8)
    button: Literal["OK", "REPEAT", "NG", "CUSTOM"]
    custom_text: Optional[str] = Field(None, max_length=4000)


@router.post("/worker-instruction-reply")
async def worker_instruction_reply_rest(
    body: WorkerInstructionReplyIn,
    token: str = Query(..., min_length=8),
) -> dict[str, bool]:
    """WS 切断時でも指示に OK/REPEAT/NG/CUSTOM で応答できる。"""
    sess = sessions.get(token)
    if sess is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    if sess.role != Role.WORKER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="worker only")
    wid = sess.workspace_id
    if not await run_db(worker_can_submit_reply, wid, body.instruction_id, sess.token, sess.staff_account_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not allowed for this instruction")
    custom_text: Optional[str] = None
    if body.button == "CUSTOM":
        if isinstance(body.custom_text, str):
            s = body.custom_text.strip()
            custom_text = s[:4000] if s else None
    ok = await run_db(
        record_reply,
        wid,
        body.instruction_id,
        sess.token,
        sess.user_label or "?",
        sess.staff_account_id,
        body.button,
        custom_text=custom_text,
    )
    if not ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid instruction")
    wlab = (sess.user_label or "").strip() or "?"
    out_msg: dict[str, Any] = {
        "type": "worker_response",
        "button": body.button,
        "ts": time.time(),
        "worker_label": wlab,
        "instruction_id": body.instruction_id,
    }
    if sess.staff_account_id:
        out_msg["staff_account_id"] = sess.staff_account_id
    if body.button == "CUSTOM" and custom_text:
        out_msg["custom_text"] = custom_text
    await manager.broadcast_json(wid, out_msg)
    return {"ok": True}


@router.post("/super-assume", response_model=SuperAssumeOut)
async def super_assume_workspace(
    body: SuperAssumeIn,
    settings: Settings = Depends(get_settings),
) -> SuperAssumeOut:
    """総運営のみ: 指定ワークスペースの管理者トークンを発行（顧客の管理者画面と同等）。"""
    s = sessions.get(body.super_token)
    if s is None or s.role != Role.SUPER_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="super admin only")
    ws = await run_db(workspaces.get, body.workspace_id)
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


class ChatMessageIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


@router.get("/chat-messages")
async def get_chat_messages(
    token: str = Query(..., min_length=8),
    limit: int = Query(80, ge=1, le=200),
) -> list[dict]:
    """ワークスペース内の簡易チャット履歴（管理者・スタッフ共通）。"""
    sess = sessions.get(token)
    if sess is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    if sess.role == Role.SUPER_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not available for super admin")
    return await run_db(chat_list_recent, sess.workspace_id, limit=limit)


@router.post("/chat-message")
async def post_chat_message(
    body: ChatMessageIn,
    token: str = Query(..., min_length=8),
) -> dict:
    """REST でもチャット送信（WS 切断時のフォールバック兼用）。サーバーが WS へも配信する。"""
    sess = sessions.get(token)
    if sess is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    if sess.role == Role.SUPER_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not available for super admin")
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="empty text")
    wid = sess.workspace_id
    ts = time.time()
    if sess.role == Role.ADMIN:
        lab = (sess.user_label or "").strip() or "管理者"
        row = await run_db(
            chat_append,
            wid,
            from_role="admin",
            from_label=lab,
            text=text,
        )
        await manager.broadcast_json_to_workers(
            wid,
            {
                "type": "admin_message",
                "text": text,
                "ts": ts,
                "from_label": lab,
                "chat_id": row["id"],
            },
        )
        return {"ok": True, "id": row["id"]}
    if sess.role == Role.WORKER:
        wlab = (sess.user_label or "").strip() or "?"
        row = await run_db(
            chat_append,
            wid,
            from_role="worker",
            from_label=wlab,
            text=text,
            staff_account_id=sess.staff_account_id,
            worker_session_token=sess.token,
        )
        out_msg: dict = {
            "type": "staff_message",
            "text": text,
            "ts": ts,
            "worker_label": wlab,
            "chat_id": row["id"],
        }
        if sess.staff_account_id:
            out_msg["staff_account_id"] = sess.staff_account_id
        await manager.broadcast_json_to_admins(wid, out_msg)
        return {"ok": True, "id": row["id"]}
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
