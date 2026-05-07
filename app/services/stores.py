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
from app.services.staff_avatar_files import delete_admin_file


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
    # 総運営一覧の並び順（小さいほど上）
    sort_order: float = 0.0
    # 現場メタ（管理者が編集、総運営一覧でも参照）
    company_name: str = ""
    branch_name: str = ""
    department_name: str = ""
    # 管理者 UI 言語: ja en ko zh vi id（ブラウザ音声認識が弱い言語は除外）
    admin_ui_locale: str = "ja"
    admin_avatar_color_index: int = 0
    admin_avatar_updated_at: Optional[float] = None


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
    aci = 0
    if "admin_avatar_color_index" in keys and row["admin_avatar_color_index"] is not None:
        try:
            aci = int(row["admin_avatar_color_index"]) % 8
        except (TypeError, ValueError):
            aci = 0
    av_ad = None
    if "admin_avatar_updated_at" in keys and row["admin_avatar_updated_at"] is not None:
        try:
            av_ad = float(row["admin_avatar_updated_at"])
        except (TypeError, ValueError):
            av_ad = None
    so = 0.0
    if "sort_order" in keys and row["sort_order"] is not None:
        try:
            so = float(row["sort_order"])
        except (TypeError, ValueError):
            so = 0.0
    return Workspace(
        id=row["id"],
        name=row["name"],
        created_at=float(row["created_at"]),
        sort_order=so,
        company_name=row["company_name"] or "",
        branch_name=row["branch_name"] or "",
        department_name=row["department_name"] or "",
        admin_ui_locale=loc,
        admin_avatar_color_index=aci,
        admin_avatar_updated_at=av_ad,
    )


class WorkspaceStore:
    def create(self, name: str) -> Workspace:
        conn = get_connection()
        ws_id = str(uuid.uuid4())
        now = time.time()
        mx_row = conn.execute("SELECT COALESCE(MAX(sort_order), -1.0) FROM workspaces").fetchone()
        try:
            mx = float(mx_row[0]) if mx_row and mx_row[0] is not None else -1.0
        except (TypeError, ValueError):
            mx = -1.0
        sort_next = mx + 1.0
        conn.execute(
            """
            INSERT INTO workspaces (id, name, created_at, company_name, branch_name, department_name, admin_ui_locale, sort_order)
            VALUES (?, ?, ?, '', '', '', 'ja', ?)
            """,
            (ws_id, name, now, sort_next),
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
        rows = conn.execute(
            "SELECT * FROM workspaces ORDER BY sort_order ASC, created_at ASC"
        ).fetchall()
        return [_row_to_workspace(r) for r in rows]

    def reorder_super(self, ordered_ids: list[str]) -> None:
        """総運営: 全ワークスペース ID をこの順で並べる。"""
        conn = get_connection()
        db_ids = {
            str(r[0])
            for r in conn.execute("SELECT id FROM workspaces").fetchall()
        }
        want = [str(x).strip() for x in ordered_ids if str(x).strip()]
        if set(want) != db_ids or len(want) != len(db_ids):
            raise ValueError("ordered_ids must list every workspace exactly once")
        for i, wid in enumerate(want):
            conn.execute(
                "UPDATE workspaces SET sort_order = ? WHERE id = ?",
                (float(i), wid),
            )
        conn.commit()

    def update_org(
        self,
        workspace_id: str,
        *,
        company_name: Optional[str] = None,
        branch_name: Optional[str] = None,
        department_name: Optional[str] = None,
        admin_ui_locale: Optional[str] = None,
        admin_avatar_color_index: Optional[int] = None,
    ) -> Optional[Workspace]:
        conn = get_connection()
        row = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        if row is None:
            return None
        sets: list[str] = []
        vals: list = []
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
        if admin_avatar_color_index is not None:
            sets.append("admin_avatar_color_index = ?")
            vals.append(int(admin_avatar_color_index) % 8)
        if sets:
            sql = "UPDATE workspaces SET " + ", ".join(sets) + " WHERE id = ?"
            vals.append(workspace_id)
            conn.execute(sql, vals)
            conn.commit()
        r = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        return _row_to_workspace(r) if r else None

    def set_admin_avatar_updated_at(self, workspace_id: str, ts: float) -> Optional[Workspace]:
        conn = get_connection()
        conn.execute(
            "UPDATE workspaces SET admin_avatar_updated_at = ? WHERE id = ?",
            (ts, workspace_id),
        )
        conn.commit()
        r = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        return _row_to_workspace(r) if r else None

    def clear_admin_avatar(self, workspace_id: str) -> Optional[Workspace]:
        delete_admin_file(workspace_id)
        conn = get_connection()
        conn.execute(
            "UPDATE workspaces SET admin_avatar_updated_at = NULL WHERE id = ?",
            (workspace_id,),
        )
        conn.commit()
        r = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        return _row_to_workspace(r) if r else None


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


# Singletons for MVP
workspaces = WorkspaceStore()
sessions = SessionStore()
