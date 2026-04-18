"""送信した指示（質問）とスタッフ別応答の履歴（SQLite）。"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from app.db.sqlite import get_connection

# 最低約60日保持（ユーザー要望は1ヶ月以上）
RETENTION_SECONDS = 60 * 24 * 60 * 60


@dataclass
class InstructionRound:
    id: str
    workspace_id: str
    text: str
    mode: str
    target_group_id: Optional[str]
    created_at: float


def _prune_old(conn) -> None:
    cutoff = time.time() - RETENTION_SECONDS
    conn.execute("DELETE FROM instruction_rounds WHERE created_at < ?", (cutoff,))


def create_round(
    workspace_id: str,
    text: str,
    mode: str,
    recipients: list[dict[str, Any]],
    target_group_id: Optional[str] = None,
) -> str:
    """recipients: [{\"token\": str, \"label\": str, \"staff_account_id\": str|None}, ...]"""
    rid = str(uuid.uuid4())
    now = time.time()
    body = (text or "").strip() or "(empty)"
    mode_norm = mode if mode in ("broadcast", "targeted", "group") else "broadcast"
    tg = (target_group_id or "").strip() or None
    conn = get_connection()
    _prune_old(conn)
    conn.execute(
        """
        INSERT INTO instruction_rounds (id, workspace_id, text, mode, target_group_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (rid, workspace_id, body, mode_norm, tg, now),
    )
    for r in recipients:
        tok = (r.get("token") or "").strip()
        if not tok:
            continue
        lab = (r.get("label") or "").strip() or "?"
        sid = r.get("staff_account_id")
        sid = sid.strip() if isinstance(sid, str) and sid.strip() else None
        conn.execute(
            """
            INSERT OR IGNORE INTO instruction_recipients
            (instruction_id, worker_token, worker_label, staff_account_id)
            VALUES (?, ?, ?, ?)
            """,
            (rid, tok, lab, sid),
        )
    conn.commit()
    return rid


def record_reply(
    workspace_id: str,
    instruction_id: str,
    worker_token: str,
    worker_label: str,
    staff_account_id: Optional[str],
    button: str,
) -> bool:
    if button not in ("OK", "REPEAT", "NG", "CUSTOM"):
        return False
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM instruction_rounds WHERE id = ? AND workspace_id = ?",
        (instruction_id, workspace_id),
    ).fetchone()
    if row is None:
        return False
    lab = (worker_label or "").strip() or "?"
    sid = staff_account_id.strip() if isinstance(staff_account_id, str) and staff_account_id.strip() else None
    now = time.time()
    conn.execute(
        """
        INSERT INTO instruction_replies
        (instruction_id, worker_token, button, worker_label, staff_account_id, responded_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(instruction_id, worker_token) DO UPDATE SET
          button = excluded.button,
          worker_label = excluded.worker_label,
          staff_account_id = excluded.staff_account_id,
          responded_at = excluded.responded_at
        """,
        (instruction_id, worker_token, button, lab, sid, now),
    )
    conn.commit()
    return True


def _summary_for_round(conn, instruction_id: str) -> dict[str, int]:
    out = {"OK": 0, "REPEAT": 0, "NG": 0, "CUSTOM": 0, "pending": 0}
    total_r = conn.execute(
        "SELECT COUNT(*) FROM instruction_recipients WHERE instruction_id = ?",
        (instruction_id,),
    ).fetchone()[0]
    rows = conn.execute(
        """
        SELECT button, COUNT(*) FROM instruction_replies WHERE instruction_id = ? GROUP BY button
        """,
        (instruction_id,),
    ).fetchall()
    reply_count = conn.execute(
        "SELECT COUNT(*) FROM instruction_replies WHERE instruction_id = ?",
        (instruction_id,),
    ).fetchone()[0]
    for btn, n in rows:
        if btn in out:
            out[btn] = int(n)
    out["pending"] = max(0, int(total_r) - int(reply_count))
    return out


def list_rounds(workspace_id: str, limit: int = 100) -> list[dict[str, Any]]:
    conn = get_connection()
    _prune_old(conn)
    conn.commit()
    rows = conn.execute(
        """
        SELECT id, text, mode, target_group_id, created_at
        FROM instruction_rounds
        WHERE workspace_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (workspace_id, max(1, min(limit, 500))),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        iid = r["id"]
        summ = _summary_for_round(conn, iid)
        out.append(
            {
                "id": iid,
                "text": r["text"] or "",
                "mode": r["mode"] or "broadcast",
                "target_group_id": r["target_group_id"],
                "created_at": float(r["created_at"]),
                "counts": summ,
            },
        )
    return out


def get_detail(workspace_id: str, instruction_id: str) -> Optional[dict[str, Any]]:
    conn = get_connection()
    row = conn.execute(
        """
        SELECT id, text, mode, target_group_id, created_at
        FROM instruction_rounds
        WHERE id = ? AND workspace_id = ?
        """,
        (instruction_id, workspace_id),
    ).fetchone()
    if row is None:
        return None
    iid = row["id"]
    summ = _summary_for_round(conn, iid)
    # 応答済み
    replied_rows = conn.execute(
        """
        SELECT worker_token, worker_label, staff_account_id, button, responded_at
        FROM instruction_replies
        WHERE instruction_id = ?
        ORDER BY responded_at ASC
        """,
        (iid,),
    ).fetchall()
    by_btn: dict[str, list[dict[str, Any]]] = {"OK": [], "REPEAT": [], "NG": [], "CUSTOM": []}
    replied_tokens: set[str] = set()
    for rr in replied_rows:
        tok = rr["worker_token"]
        replied_tokens.add(tok)
        btn = rr["button"]
        item = {
            "worker_token": tok,
            "label": rr["worker_label"] or "?",
            "staff_account_id": rr["staff_account_id"],
            "responded_at": float(rr["responded_at"]),
        }
        if btn in by_btn:
            by_btn[btn].append(item)
    # 未応答（受信者にいたが返信なし）
    pending: list[dict[str, Any]] = []
    rec_rows = conn.execute(
        """
        SELECT worker_token, worker_label, staff_account_id
        FROM instruction_recipients
        WHERE instruction_id = ?
        """,
        (iid,),
    ).fetchall()
    for rec in rec_rows:
        tok = rec["worker_token"]
        if tok in replied_tokens:
            continue
        pending.append(
            {
                "worker_token": tok,
                "label": rec["worker_label"] or "?",
                "staff_account_id": rec["staff_account_id"],
            },
        )
    return {
        "id": iid,
        "text": row["text"] or "",
        "mode": row["mode"] or "broadcast",
        "target_group_id": row["target_group_id"],
        "created_at": float(row["created_at"]),
        "counts": summ,
        "by_button": by_btn,
        "pending": pending,
    }
