"""ワークスペース単位の個人スタッフログイン（SQLite）。スタッフは個人アカウント必須。"""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

_MISSING = object()
# PATCH でフィールドを更新しないとき workspaces ルートから渡す
PATCH_OMIT = _MISSING
GROUP_FIELD_UNSET = _MISSING  # 互換alias

from passlib.context import CryptContext

from app.db.sqlite import get_connection
from app.services.staff_avatar_files import delete_file

# bcrypt と passlib の組み合わせで環境により失敗するため、標準互換の PBKDF2 を使用
_pwd = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

# UI と共通（admin.html の AVATAR_THEME_COLORS と揃える）
AVATAR_COLOR_COUNT = 8


@dataclass
class StaffAccount:
    id: str
    workspace_id: str
    login_id: str
    display_name: str
    password_hash: str
    created_at: float
    group_id: Optional[str] = None
    profile_phone: str = ""
    profile_email: str = ""
    avatar_color_index: int = 0
    avatar_updated_at: Optional[float] = None


def _row(row) -> StaffAccount:
    keys = row.keys()
    gid = None
    if "group_id" in keys and row["group_id"]:
        gid = str(row["group_id"]).strip() or None
    phone = ""
    if "profile_phone" in keys and row["profile_phone"]:
        phone = str(row["profile_phone"]).strip()
    email = ""
    if "profile_email" in keys and row["profile_email"]:
        email = str(row["profile_email"]).strip()
    aci = 0
    if "avatar_color_index" in keys and row["avatar_color_index"] is not None:
        try:
            aci = int(row["avatar_color_index"]) % AVATAR_COLOR_COUNT
        except (TypeError, ValueError):
            aci = 0
    av_t = None
    if "avatar_updated_at" in keys and row["avatar_updated_at"] is not None:
        try:
            av_t = float(row["avatar_updated_at"])
        except (TypeError, ValueError):
            av_t = None
    return StaffAccount(
        id=row["id"],
        workspace_id=row["workspace_id"],
        login_id=row["login_id"],
        display_name=row["display_name"] or "",
        password_hash=row["password_hash"],
        created_at=float(row["created_at"]),
        group_id=gid,
        profile_phone=phone,
        profile_email=email,
        avatar_color_index=aci,
        avatar_updated_at=av_t,
    )


class StaffAccountStore:
    def list_for_workspace(self, workspace_id: str) -> list[StaffAccount]:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT * FROM workspace_staff_accounts
            WHERE workspace_id = ?
            ORDER BY group_id IS NULL, group_id, lower(login_id) ASC
            """,
            (workspace_id,),
        ).fetchall()
        return [_row(r) for r in rows]

    def get(self, account_id: str) -> Optional[StaffAccount]:
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM workspace_staff_accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        return _row(row) if row else None

    def get_by_workspace_login(self, workspace_id: str, login_id: str) -> Optional[StaffAccount]:
        key = login_id.strip().lower()
        if not key:
            return None
        conn = get_connection()
        row = conn.execute(
            """
            SELECT * FROM workspace_staff_accounts
            WHERE workspace_id = ? AND lower(trim(login_id)) = ?
            """,
            (workspace_id, key),
        ).fetchone()
        return _row(row) if row else None

    def verify_password(self, plain: str, password_hash: str) -> bool:
        return _pwd.verify(plain, password_hash)

    def list_account_ids_in_group(self, workspace_id: str, group_id: str) -> list[str]:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT id FROM workspace_staff_accounts
            WHERE workspace_id = ? AND group_id = ?
            """,
            (workspace_id, group_id),
        ).fetchall()
        return [str(r["id"]) for r in rows]

    def create(
        self,
        workspace_id: str,
        login_id: str,
        display_name: str,
        plain_password: str,
        group_id: Optional[str] = None,
    ) -> StaffAccount:
        lid = login_id.strip()
        if not lid:
            raise ValueError("login_id required")
        if self.get_by_workspace_login(workspace_id, lid):
            raise ValueError("login_id already exists")
        aid = str(uuid.uuid4())
        now = time.time()
        h = _pwd.hash(plain_password)
        from app.db.sqlite import is_unique_violation
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO workspace_staff_accounts
                (id, workspace_id, login_id, display_name, password_hash, created_at, group_id,
                 profile_phone, profile_email, avatar_color_index, avatar_updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, '', '', 0, NULL)
                """,
                (aid, workspace_id, lid, display_name.strip(), h, now, group_id),
            )
        except Exception as e:
            # SQLite / Postgres 両方の UNIQUE 違反を捕捉。
            if is_unique_violation(e):
                raise ValueError("login_id already exists") from e
            raise
        conn.commit()
        row = conn.execute("SELECT * FROM workspace_staff_accounts WHERE id = ?", (aid,)).fetchone()
        assert row is not None
        return _row(row)

    def update(
        self,
        account_id: str,
        workspace_id: str,
        *,
        display_name: Any = _MISSING,
        plain_password: Any = _MISSING,
        group_id: Any = _MISSING,
        profile_phone: Any = _MISSING,
        profile_email: Any = _MISSING,
        avatar_color_index: Any = _MISSING,
        avatar_updated_at: Any = _MISSING,
    ) -> Optional[StaffAccount]:
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM workspace_staff_accounts WHERE id = ? AND workspace_id = ?",
            (account_id, workspace_id),
        ).fetchone()
        if row is None:
            return None
        sets: list[str] = []
        vals: list = []
        if display_name is not _MISSING:
            sets.append("display_name = ?")
            vals.append(display_name.strip())
        if plain_password is not _MISSING:
            sets.append("password_hash = ?")
            vals.append(_pwd.hash(plain_password))
        if group_id is not _MISSING:
            sets.append("group_id = ?")
            vals.append(group_id)
        if profile_phone is not _MISSING:
            sets.append("profile_phone = ?")
            vals.append((profile_phone or "").strip())
        if profile_email is not _MISSING:
            sets.append("profile_email = ?")
            vals.append((profile_email or "").strip())
        if avatar_color_index is not _MISSING:
            try:
                ai = int(avatar_color_index) % AVATAR_COLOR_COUNT
            except (TypeError, ValueError):
                ai = 0
            sets.append("avatar_color_index = ?")
            vals.append(ai)
        if avatar_updated_at is not _MISSING:
            sets.append("avatar_updated_at = ?")
            vals.append(avatar_updated_at)
        if not sets:
            return _row(row)
        vals.append(account_id)
        conn.execute(
            "UPDATE workspace_staff_accounts SET " + ", ".join(sets) + " WHERE id = ?",
            vals,
        )
        conn.commit()
        r = conn.execute("SELECT * FROM workspace_staff_accounts WHERE id = ?", (account_id,)).fetchone()
        return _row(r) if r else None

    def clear_avatar_image(self, account_id: str, workspace_id: str) -> bool:
        """ファイル削除 + avatar_updated_at を NULL に。"""
        conn = get_connection()
        row = conn.execute(
            "SELECT id FROM workspace_staff_accounts WHERE id = ? AND workspace_id = ?",
            (account_id, workspace_id),
        ).fetchone()
        if row is None:
            return False
        delete_file(account_id)
        conn.execute(
            "UPDATE workspace_staff_accounts SET avatar_updated_at = NULL WHERE id = ?",
            (account_id,),
        )
        conn.commit()
        return True

    def delete(self, account_id: str, workspace_id: str) -> bool:
        delete_file(account_id)
        conn = get_connection()
        cur = conn.execute(
            "DELETE FROM workspace_staff_accounts WHERE id = ? AND workspace_id = ?",
            (account_id, workspace_id),
        )
        conn.commit()
        return cur.rowcount > 0


staff_accounts = StaffAccountStore()
