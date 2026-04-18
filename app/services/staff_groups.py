"""スタッフグループ（フォルダ）— ワークスペース単位。"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Optional

from app.db.sqlite import get_connection


@dataclass
class StaffGroup:
    id: str
    workspace_id: str
    name: str
    sort_order: float
    created_at: float


def _row(r) -> StaffGroup:
    return StaffGroup(
        id=r["id"],
        workspace_id=r["workspace_id"],
        name=r["name"] or "",
        sort_order=float(r["sort_order"] or 0),
        created_at=float(r["created_at"]),
    )


class StaffGroupStore:
    def list_for_workspace(self, workspace_id: str) -> list[StaffGroup]:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT * FROM staff_groups
            WHERE workspace_id = ?
            ORDER BY sort_order ASC, lower(name) ASC
            """,
            (workspace_id,),
        ).fetchall()
        return [_row(r) for r in rows]

    def get(self, group_id: str, workspace_id: str) -> Optional[StaffGroup]:
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM staff_groups WHERE id = ? AND workspace_id = ?",
            (group_id, workspace_id),
        ).fetchone()
        return _row(row) if row else None

    def create(self, workspace_id: str, name: str) -> StaffGroup:
        nm = name.strip()
        if not nm:
            raise ValueError("name required")
        gid = str(uuid.uuid4())
        now = time.time()
        conn = get_connection()
        mx = conn.execute(
            "SELECT MAX(sort_order) FROM staff_groups WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()[0]
        sort_order = float(mx or 0) + 1.0
        conn.execute(
            """
            INSERT INTO staff_groups (id, workspace_id, name, sort_order, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (gid, workspace_id, nm, sort_order, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM staff_groups WHERE id = ?", (gid,)).fetchone()
        assert row is not None
        return _row(row)

    def rename(self, group_id: str, workspace_id: str, name: str) -> Optional[StaffGroup]:
        nm = name.strip()
        if not nm:
            raise ValueError("name required")
        conn = get_connection()
        cur = conn.execute(
            "UPDATE staff_groups SET name = ? WHERE id = ? AND workspace_id = ?",
            (nm, group_id, workspace_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
        row = conn.execute(
            "SELECT * FROM staff_groups WHERE id = ? AND workspace_id = ?",
            (group_id, workspace_id),
        ).fetchone()
        return _row(row) if row else None

    def delete(self, group_id: str, workspace_id: str) -> bool:
        conn = get_connection()
        row = conn.execute(
            "SELECT id FROM staff_groups WHERE id = ? AND workspace_id = ?",
            (group_id, workspace_id),
        ).fetchone()
        if row is None:
            return False
        conn.execute(
            "UPDATE workspace_staff_accounts SET group_id = NULL WHERE group_id = ?",
            (group_id,),
        )
        conn.execute(
            "DELETE FROM staff_groups WHERE id = ? AND workspace_id = ?",
            (group_id, workspace_id),
        )
        conn.commit()
        return True


staff_groups = StaffGroupStore()
