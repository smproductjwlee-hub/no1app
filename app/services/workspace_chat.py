from __future__ import annotations

import time
import uuid
from typing import Optional

from app.db.sqlite import get_connection


def append(
    workspace_id: str,
    *,
    from_role: str,
    from_label: str,
    text: str,
    staff_account_id: Optional[str] = None,
    worker_session_token: Optional[str] = None,
) -> dict:
    mid = str(uuid.uuid4())
    now = time.time()
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO workspace_chat_messages (
          id, workspace_id, from_role, worker_session_token, staff_account_id, from_label, text, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mid,
            workspace_id,
            from_role,
            worker_session_token,
            staff_account_id,
            (from_label or "").strip()[:200],
            text.strip()[:4000],
            now,
        ),
    )
    conn.commit()
    return {
        "id": mid,
        "workspace_id": workspace_id,
        "from_role": from_role,
        "from_label": (from_label or "").strip(),
        "text": text.strip()[:4000],
        "created_at": now,
        "staff_account_id": staff_account_id,
    }


def list_recent(workspace_id: str, *, limit: int = 80) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT id, workspace_id, from_role, from_label, text, created_at, staff_account_id
        FROM workspace_chat_messages
        WHERE workspace_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (workspace_id, limit),
    ).fetchall()
    out: list[dict] = []
    for r in reversed(rows):
        out.append(
            {
                "id": str(r["id"]),
                "from_role": str(r["from_role"]),
                "from_label": str(r["from_label"] or ""),
                "text": str(r["text"] or ""),
                "created_at": float(r["created_at"]),
                "staff_account_id": str(r["staff_account_id"]) if r["staff_account_id"] else None,
            },
        )
    return out
