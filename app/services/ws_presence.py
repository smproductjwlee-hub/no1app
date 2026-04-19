from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from app.db.sqlite import get_connection
from app.services.stores import Role


@dataclass(frozen=True)
class PresenceRow:
    token: str
    label: str
    staff_account_id: Optional[str]


class WsPresenceStore:
    def upsert(
        self,
        workspace_id: str,
        *,
        session_token: str,
        role: Role,
        user_label: Optional[str],
        staff_account_id: Optional[str],
    ) -> None:
        now = time.time()
        conn = get_connection()
        conn.execute(
            """
            INSERT INTO ws_presence (
              workspace_id, session_token, role, user_label, staff_account_id, connected_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(workspace_id, session_token) DO UPDATE SET
              role = excluded.role,
              user_label = excluded.user_label,
              staff_account_id = excluded.staff_account_id,
              last_seen_at = excluded.last_seen_at
            """,
            (
                workspace_id,
                session_token,
                role.value,
                (user_label or "").strip(),
                staff_account_id,
                now,
                now,
            ),
        )
        conn.commit()

    def touch(self, workspace_id: str, *, session_token: str) -> None:
        conn = get_connection()
        conn.execute(
            "UPDATE ws_presence SET last_seen_at = ? WHERE workspace_id = ? AND session_token = ?",
            (time.time(), workspace_id, session_token),
        )
        conn.commit()

    def delete(self, workspace_id: str, *, session_token: str) -> None:
        conn = get_connection()
        conn.execute(
            "DELETE FROM ws_presence WHERE workspace_id = ? AND session_token = ?",
            (workspace_id, session_token),
        )
        conn.commit()

    def cleanup_stale(self, *, stale_seconds: int = 120) -> None:
        cutoff = time.time() - stale_seconds
        conn = get_connection()
        conn.execute("DELETE FROM ws_presence WHERE last_seen_at < ?", (cutoff,))
        conn.commit()

    def list_online_workers(self, workspace_id: str) -> list[PresenceRow]:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT session_token, user_label, staff_account_id
            FROM ws_presence
            WHERE workspace_id = ? AND role = ?
            ORDER BY last_seen_at DESC
            """,
            (workspace_id, Role.WORKER.value),
        ).fetchall()
        return [
            PresenceRow(
                token=str(r["session_token"]),
                label=(str(r["user_label"] or "")).strip() or "スタッフ",
                staff_account_id=str(r["staff_account_id"]) if r["staff_account_id"] else None,
            )
            for r in rows
        ]


ws_presence = WsPresenceStore()

