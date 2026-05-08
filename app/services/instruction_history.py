"""送信した指示（質問）とスタッフ別応答の履歴（SQLite）。"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from app.db.sqlite import get_connection
from app.services.staff_accounts import staff_accounts

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
    image_url: Optional[str] = None


def _prune_old(conn) -> None:
    cutoff = time.time() - RETENTION_SECONDS
    conn.execute("DELETE FROM instruction_rounds WHERE created_at < ?", (cutoff,))


def create_round(
    workspace_id: str,
    text: str,
    mode: str,
    recipients: list[dict[str, Any]],
    target_group_id: Optional[str] = None,
    image_url: Optional[str] = None,
) -> str:
    """recipients: [{\"token\": str, \"label\": str, \"staff_account_id\": str|None}, ...]"""
    rid = str(uuid.uuid4())
    now = time.time()
    t = (text or "").strip()
    img = (image_url or "").strip() or None
    if not t and not img:
        body = "(empty)"
    else:
        body = t
    mode_norm = mode if mode in ("broadcast", "targeted", "group") else "broadcast"
    tg = (target_group_id or "").strip() or None
    conn = get_connection()
    _prune_old(conn)
    conn.execute(
        """
        INSERT INTO instruction_rounds (id, workspace_id, text, mode, target_group_id, created_at, image_url)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (rid, workspace_id, body, mode_norm, tg, now, img),
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
            INSERT INTO instruction_recipients
            (instruction_id, worker_token, worker_label, staff_account_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT DO NOTHING
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
    custom_text: Optional[str] = None,
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
    ct: Optional[str] = None
    if button == "CUSTOM":
        if isinstance(custom_text, str):
            s = custom_text.strip()
            ct = s[:4000] if s else None
    conn.execute(
        """
        INSERT INTO instruction_replies
        (instruction_id, worker_token, button, worker_label, staff_account_id, responded_at, custom_text)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(instruction_id, worker_token) DO UPDATE SET
          button = excluded.button,
          worker_label = excluded.worker_label,
          staff_account_id = excluded.staff_account_id,
          responded_at = excluded.responded_at,
          custom_text = excluded.custom_text
        """,
        (instruction_id, worker_token, button, lab, sid, now, ct),
    )
    conn.commit()
    return True


def list_worker_instruction_history(
    workspace_id: str,
    worker_token: str,
    limit: int = 80,
    staff_account_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """このワーカー（個人スタッフ ID または現セッショントークン）が返信した指示の一覧（新しい順）。"""
    conn = get_connection()
    _prune_old(conn)
    conn.commit()
    lim = max(1, min(int(limit), 200))
    sid = staff_account_id.strip() if isinstance(staff_account_id, str) and staff_account_id.strip() else None
    rows = conn.execute(
        """
        WITH my_replies AS (
          SELECT instruction_id, button, custom_text, responded_at,
                 ROW_NUMBER() OVER (
                   PARTITION BY instruction_id ORDER BY responded_at DESC
                 ) AS rn
          FROM instruction_replies
          WHERE worker_token = ? OR (? IS NOT NULL AND staff_account_id = ?)
        )
        SELECT
          r.id AS instruction_id,
          r.text AS text,
          r.image_url AS image_url,
          r.created_at AS created_at,
          rep.button AS button,
          rep.custom_text AS custom_text,
          rep.responded_at AS responded_at
        FROM instruction_rounds r
        INNER JOIN my_replies rep ON rep.instruction_id = r.id AND rep.rn = 1
        WHERE r.workspace_id = ?
        ORDER BY r.created_at DESC
        LIMIT ?
        """,
        (worker_token, sid, sid, workspace_id, lim),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        img = row["image_url"] if row["image_url"] else None
        out.append(
            {
                "instruction_id": row["instruction_id"],
                "text": row["text"] or "",
                "image_url": img,
                "created_at": float(row["created_at"]),
                "button": row["button"] or "",
                "custom_text": row["custom_text"] if row["custom_text"] else None,
                "responded_at": float(row["responded_at"]),
            },
        )
    return out


def list_worker_instruction_history_ng_only(
    workspace_id: str,
    worker_token: str,
    limit: int = 80,
    staff_account_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """「分からない」(button=NG) の返信だけ（新しい順）。最新の応答が NG のみ含む。"""
    conn = get_connection()
    _prune_old(conn)
    conn.commit()
    lim = max(1, min(int(limit), 200))
    sid = staff_account_id.strip() if isinstance(staff_account_id, str) and staff_account_id.strip() else None
    rows = conn.execute(
        """
        WITH my_replies AS (
          SELECT instruction_id, button, custom_text, responded_at,
                 ROW_NUMBER() OVER (
                   PARTITION BY instruction_id ORDER BY responded_at DESC
                 ) AS rn
          FROM instruction_replies
          WHERE worker_token = ? OR (? IS NOT NULL AND staff_account_id = ?)
        )
        SELECT
          r.id AS instruction_id,
          r.text AS text,
          r.image_url AS image_url,
          r.created_at AS created_at,
          rep.button AS button,
          rep.custom_text AS custom_text,
          rep.responded_at AS responded_at
        FROM instruction_rounds r
        INNER JOIN my_replies rep ON rep.instruction_id = r.id AND rep.rn = 1
        WHERE r.workspace_id = ? AND rep.button = 'NG'
        ORDER BY r.created_at DESC
        LIMIT ?
        """,
        (worker_token, sid, sid, workspace_id, lim),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        img = row["image_url"] if row["image_url"] else None
        out.append(
            {
                "instruction_id": row["instruction_id"],
                "text": row["text"] or "",
                "image_url": img,
                "created_at": float(row["created_at"]),
                "button": row["button"] or "",
                "custom_text": row["custom_text"] if row["custom_text"] else None,
                "responded_at": float(row["responded_at"]),
            },
        )
    return out


def _instruction_eligible_for_worker(
    conn,
    workspace_id: str,
    row,
    worker_token: str,
    staff_account_id: Optional[str],
) -> bool:
    """送信時の受信者リスト・broadcast・グループ所属に基づき、このワーカーが応答できるか。"""
    mode = (row["mode"] or "broadcast").strip()
    iid = row["id"]
    if mode == "broadcast":
        return True
    sid = staff_account_id.strip() if isinstance(staff_account_id, str) and staff_account_id.strip() else None
    hit = conn.execute(
        """
        SELECT 1 FROM instruction_recipients
        WHERE instruction_id = ?
          AND (
            worker_token = ?
            OR (? IS NOT NULL AND staff_account_id IS NOT NULL AND staff_account_id = ?)
          )
        LIMIT 1
        """,
        (iid, worker_token, sid, sid),
    ).fetchone()
    if hit:
        return True
    if mode == "group" and sid:
        acc = staff_accounts.get(sid)
        if acc and acc.workspace_id == workspace_id and acc.group_id:
            tg = (row["target_group_id"] or "").strip()
            if tg:
                allowed = {g.strip() for g in tg.split(",") if g.strip()}
                if acc.group_id in allowed:
                    return True
    return False


def worker_can_submit_reply(
    workspace_id: str,
    instruction_id: str,
    worker_token: str,
    staff_account_id: Optional[str],
) -> bool:
    conn = get_connection()
    row = conn.execute(
        """
        SELECT id, mode, target_group_id FROM instruction_rounds
        WHERE id = ? AND workspace_id = ?
        """,
        (instruction_id, workspace_id),
    ).fetchone()
    if row is None:
        return False
    sid = staff_account_id.strip() if isinstance(staff_account_id, str) and staff_account_id.strip() else None
    prev = conn.execute(
        """
        SELECT 1 FROM instruction_replies
        WHERE instruction_id = ?
          AND (
            worker_token = ?
            OR (? IS NOT NULL AND staff_account_id = ?)
          )
        """,
        (instruction_id, worker_token, sid, sid),
    ).fetchone()
    if prev:
        return True
    return _instruction_eligible_for_worker(conn, workspace_id, row, worker_token, staff_account_id)


def list_pending_instructions_for_worker(
    workspace_id: str,
    worker_token: str,
    staff_account_id: Optional[str],
    limit: int = 50,
) -> list[dict[str, Any]]:
    """未返信の指示（遅延ログイン・broadcast など）。古い順。"""
    conn = get_connection()
    _prune_old(conn)
    conn.commit()
    lim = max(1, min(int(limit), 100))
    sid = staff_account_id.strip() if isinstance(staff_account_id, str) and staff_account_id.strip() else None
    rows = conn.execute(
        """
        SELECT id, text, image_url, created_at, mode, target_group_id
        FROM instruction_rounds r
        WHERE r.workspace_id = ?
        AND NOT EXISTS (
          SELECT 1 FROM instruction_replies rep
          WHERE rep.instruction_id = r.id
            AND (
              rep.worker_token = ?
              OR (? IS NOT NULL AND rep.staff_account_id = ?)
            )
        )
        ORDER BY r.created_at ASC
        LIMIT 400
        """,
        (workspace_id, worker_token, sid, sid),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        if len(out) >= lim:
            break
        if _instruction_eligible_for_worker(conn, workspace_id, row, worker_token, sid):
            img = row["image_url"] if row["image_url"] else None
            out.append(
                {
                    "instruction_id": row["id"],
                    "text": row["text"] or "",
                    "image_url": img,
                    "created_at": float(row["created_at"]),
                    "mode": row["mode"] or "broadcast",
                },
            )
    return out


def list_recent_eligible_instructions(
    workspace_id: str,
    worker_token: str,
    staff_account_id: Optional[str],
    *,
    limit: int = 10,
    scan_cap: int = 400,
) -> list[dict[str, Any]]:
    """対象となる指示ラウンドを新しい順に返す（返信済みも含む）。ログイン直後の最新表示用。"""
    conn = get_connection()
    _prune_old(conn)
    conn.commit()
    sid = staff_account_id.strip() if isinstance(staff_account_id, str) and staff_account_id.strip() else None
    lim = max(1, min(int(limit), 50))
    cap = max(50, min(int(scan_cap), 800))
    rows = conn.execute(
        """
        SELECT id, text, image_url, created_at, mode, target_group_id
        FROM instruction_rounds
        WHERE workspace_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (workspace_id, cap),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        if len(out) >= lim:
            break
        if _instruction_eligible_for_worker(conn, workspace_id, row, worker_token, sid):
            img = row["image_url"] if row["image_url"] else None
            out.append(
                {
                    "instruction_id": row["id"],
                    "text": row["text"] or "",
                    "image_url": img,
                    "created_at": float(row["created_at"]),
                    "mode": row["mode"] or "broadcast",
                },
            )
    return out


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
        SELECT id, text, image_url, mode, target_group_id, created_at
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
        img = r["image_url"] if r["image_url"] else None
        out.append(
            {
                "id": iid,
                "text": r["text"] or "",
                "image_url": img,
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
        SELECT id, text, image_url, mode, target_group_id, created_at
        FROM instruction_rounds
        WHERE id = ? AND workspace_id = ?
        """,
        (instruction_id, workspace_id),
    ).fetchone()
    if row is None:
        return None
    iid = row["id"]
    summ = _summary_for_round(conn, iid)
    img = row["image_url"] if row["image_url"] else None
    # 応答済み
    replied_rows = conn.execute(
        """
        SELECT worker_token, worker_label, staff_account_id, button, responded_at, custom_text
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
            "custom_text": rr["custom_text"] if rr["custom_text"] else None,
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
        "image_url": img,
        "mode": row["mode"] or "broadcast",
        "target_group_id": row["target_group_id"],
        "created_at": float(row["created_at"]),
        "counts": summ,
        "by_button": by_btn,
        "pending": pending,
    }
