"""ワークスペースは SQLite ファイルで永続化。その他はセッション用メモリ。"""

from __future__ import annotations

import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from app.db.sqlite import get_connection


# 総運営スーパー管理者セッション用（WS 接続不可・顧客ワークスペース選択のみ）
SUPER_WORKSPACE_ID = "__wb_super__"


class Role(str, Enum):
    ADMIN = "admin"
    WORKER = "worker"
    SUPER_ADMIN = "super_admin"


@dataclass
class Workspace:
    id: str
    name: str
    created_at: float = field(default_factory=time.time)
    # 現場メタ（管理者が編集、総運営一覧でも参照）
    company_name: str = ""
    branch_name: str = ""
    department_name: str = ""
    # 管理者 UI 言語: ja en ko zh vi id（ブラウザ音声認識が弱い言語は除外）
    admin_ui_locale: str = "ja"


@dataclass
class JoinToken:
    token: str
    workspace_id: str
    expires_at: float


@dataclass
class Session:
    token: str
    workspace_id: str
    role: Role
    user_label: Optional[str]
    expires_at: float
    # 個人アカウントログイン時のみ（グループ送信・一覧用）
    staff_account_id: Optional[str] = None


def _row_to_workspace(row: sqlite3.Row) -> Workspace:
    keys = row.keys()
    loc = "ja"
    if "admin_ui_locale" in keys and row["admin_ui_locale"]:
        loc = str(row["admin_ui_locale"]).strip() or "ja"
    return Workspace(
        id=row["id"],
        name=row["name"],
        created_at=float(row["created_at"]),
        company_name=row["company_name"] or "",
        branch_name=row["branch_name"] or "",
        department_name=row["department_name"] or "",
        admin_ui_locale=loc,
    )


class WorkspaceStore:
    def create(self, name: str) -> Workspace:
        conn = get_connection()
        ws_id = str(uuid.uuid4())
        now = time.time()
        conn.execute(
            """
            INSERT INTO workspaces (id, name, created_at, company_name, branch_name, department_name, admin_ui_locale)
            VALUES (?, ?, ?, '', '', '', 'ja')
            """,
            (ws_id, name, now),
        )
        conn.commit()
        r = conn.execute("SELECT * FROM workspaces WHERE id = ?", (ws_id,)).fetchone()
        assert r is not None
        return _row_to_workspace(r)

    def get(self, workspace_id: str) -> Optional[Workspace]:
        conn = get_connection()
        row = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        return _row_to_workspace(row) if row else None

    def find_by_name(self, name: str) -> Optional[Workspace]:
        """大文字小文字を区別せず名前一致で検索（MVP）。"""
        key = name.strip().lower()
        if not key:
            return None
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM workspaces WHERE lower(trim(name)) = ? LIMIT 1",
            (key,),
        ).fetchone()
        return _row_to_workspace(row) if row else None

    def list_all(self) -> list[Workspace]:
        conn = get_connection()
        rows = conn.execute("SELECT * FROM workspaces ORDER BY created_at ASC").fetchall()
        return [_row_to_workspace(r) for r in rows]

    def update_org(
        self,
        workspace_id: str,
        *,
        company_name: Optional[str] = None,
        branch_name: Optional[str] = None,
        department_name: Optional[str] = None,
        admin_ui_locale: Optional[str] = None,
    ) -> Optional[Workspace]:
        conn = get_connection()
        row = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        if row is None:
            return None
        sets: list[str] = []
        vals: list[str] = []
        if company_name is not None:
            sets.append("company_name = ?")
            vals.append(company_name.strip())
        if branch_name is not None:
            sets.append("branch_name = ?")
            vals.append(branch_name.strip())
        if department_name is not None:
            sets.append("department_name = ?")
            vals.append(department_name.strip())
        if admin_ui_locale is not None:
            sets.append("admin_ui_locale = ?")
            vals.append(admin_ui_locale.strip() or "ja")
        if sets:
            sql = "UPDATE workspaces SET " + ", ".join(sets) + " WHERE id = ?"
            vals.append(workspace_id)
            conn.execute(sql, vals)
            conn.commit()
        r = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        return _row_to_workspace(r) if r else None


class JoinTokenStore:
    def __init__(self) -> None:
        self._by_token: dict[str, JoinToken] = {}

    def issue(self, workspace_id: str, ttl_seconds: int) -> JoinToken:
        raw = secrets.token_urlsafe(24)
        jt = JoinToken(
            token=raw,
            workspace_id=workspace_id,
            expires_at=time.time() + ttl_seconds,
        )
        self._by_token[raw] = jt
        return jt

    def consume(self, token: str) -> Optional[JoinToken]:
        jt = self._by_token.pop(token, None)
        if jt is None:
            return None
        if time.time() > jt.expires_at:
            return None
        return jt


class SessionStore:
    def __init__(self) -> None:
        self._by_token: dict[str, Session] = {}

    def create(
        self,
        workspace_id: str,
        role: Role,
        user_label: Optional[str],
        ttl_seconds: int,
        staff_account_id: Optional[str] = None,
    ) -> Session:
        raw = secrets.token_urlsafe(32)
        sess = Session(
            token=raw,
            workspace_id=workspace_id,
            role=role,
            user_label=user_label,
            expires_at=time.time() + ttl_seconds,
            staff_account_id=staff_account_id,
        )
        self._by_token[raw] = sess
        return sess

    def get(self, token: str) -> Optional[Session]:
        sess = self._by_token.get(token)
        if sess is None:
            return None
        if time.time() > sess.expires_at:
            self._by_token.pop(token, None)
            return None
        return sess


class WorkerNumberStore:
    """워크스페이스별 근로자 자동 번호 (MVP 메모리, 서버 재시작 시 1부터 다시)."""

    def __init__(self) -> None:
        self._next: dict[str, int] = {}

    def next(self, workspace_id: str) -> int:
        n = self._next.get(workspace_id, 1)
        self._next[workspace_id] = n + 1
        return n


# Singletons for MVP
workspaces = WorkspaceStore()
join_tokens = JoinTokenStore()
sessions = SessionStore()
worker_numbers = WorkerNumberStore()
