from __future__ import annotations

import time
import uuid
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field

from app.services.workspace_chat import append as chat_append, list_recent as chat_list_recent

from app.core.config import Settings, get_settings
from app.services.instruction_history import (
    list_pending_instructions_for_worker,
    list_recent_eligible_instructions,
    list_worker_instruction_history,
    list_worker_instruction_history_ng_only,
    record_reply,
    worker_can_submit_reply,
)
from app.services.staff_avatar_files import save_square_jpeg
from app.services.worker_glossary_saves import worker_glossary_saves
from app.services.workspace_glossary_terms import workspace_glossary_terms
from app.services.staff_accounts import staff_accounts
from app.services.stores import (
    SUPER_WORKSPACE_ID,
    Role,
    join_tokens,
    sessions,
    worker_numbers,
    workspaces,
)
from app.ws.manager import manager

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
    out: dict = {
        "workspace_id": sess.workspace_id,
        "workspace_name": ws.name if ws else None,
        "role": sess.role.value,
    }
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
    acc = staff_accounts.get(sess.staff_account_id)
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
    acc = staff_accounts.get(sess.staff_account_id)
    if acc is None or acc.workspace_id != sess.workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    if not staff_accounts.verify_password(body.current_password, acc.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid current password")
    staff_accounts.update(acc.id, sess.workspace_id, plain_password=body.new_password)
    return {"ok": True}


@router.post("/worker-avatar")
async def worker_upload_own_avatar(
    token: str = Query(..., min_length=8),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    sess = _require_worker_session(token)
    if not sess.staff_account_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="staff account required")
    acc = staff_accounts.get(sess.staff_account_id)
    if acc is None or acc.workspace_id != sess.workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="empty file")
    try:
        ts = save_square_jpeg(acc.id, content)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    a = staff_accounts.update(acc.id, sess.workspace_id, avatar_updated_at=ts)
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
    acc = staff_accounts.get(sess.staff_account_id)
    if acc is None or acc.workspace_id != sess.workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    staff_accounts.clear_avatar_image(acc.id, sess.workspace_id)
    return {"ok": True}


@router.get("/worker-ng-replies")
async def worker_ng_replies_only(
    token: str = Query(..., min_length=8),
    limit: int = Query(80, ge=1, le=200),
) -> list[dict]:
    sess = _require_worker_session(token)
    return list_worker_instruction_history_ng_only(sess.workspace_id, sess.token, limit=limit)


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
    return worker_glossary_saves.list(sess.workspace_id, sess.staff_account_id, kind, limit=limit)


@router.post("/worker-glossary-saves")
async def worker_add_glossary_save(
    body: WorkerGlossarySaveIn,
    token: str = Query(..., min_length=8),
) -> dict[str, Any]:
    sess = _require_worker_session(token)
    if not sess.staff_account_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="staff account required")
    row = worker_glossary_saves.add(
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
    ok = worker_glossary_saves.delete(sess.workspace_id, sess.staff_account_id, save_id)
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
    return workspace_glossary_terms.merged_food_glossary(sess.workspace_id, settings, sheet_gid)


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
    return list_worker_instruction_history(sess.workspace_id, sess.token, limit=limit)


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
    return list_pending_instructions_for_worker(
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
    return list_recent_eligible_instructions(
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
    if not worker_can_submit_reply(wid, body.instruction_id, sess.token, sess.staff_account_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not allowed for this instruction")
    custom_text: Optional[str] = None
    if body.button == "CUSTOM":
        if isinstance(body.custom_text, str):
            s = body.custom_text.strip()
            custom_text = s[:4000] if s else None
    ok = record_reply(
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
    return chat_list_recent(sess.workspace_id, limit=limit)


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
        row = chat_append(
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
        row = chat_append(
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
