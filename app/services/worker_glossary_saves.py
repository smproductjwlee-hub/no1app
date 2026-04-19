"""スタッフ個人アカウント向け：単語帳・表現帳（外食用語シート行の保存）。"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Literal

from app.db.sqlite import get_connection

Kind = Literal["word", "expression"]


def _item_hash(item: dict[str, str], sheet_gid: int) -> str:
    canonical = json.dumps({"g": sheet_gid, "row": item}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def list_saves(workspace_id: str, staff_account_id: str, kind: Kind, *, limit: int = 200) -> list[dict[str, Any]]:
    conn = get_connection()
    lim = max(1, min(int(limit), 500))
    rows = conn.execute(
        """
        SELECT id, sheet_gid, item_json, created_at
        FROM worker_glossary_saves
        WHERE workspace_id = ? AND staff_account_id = ? AND kind = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (workspace_id, staff_account_id, kind, lim),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            item = json.loads(r["item_json"] or "{}")
        except json.JSONDecodeError:
            item = {}
        out.append(
            {
                "id": r["id"],
                "sheet_gid": int(r["sheet_gid"] or 0),
                "item": item,
                "created_at": float(r["created_at"]),
            },
        )
    return out


def add_save(
    workspace_id: str,
    staff_account_id: str,
    kind: Kind,
    sheet_gid: int,
    item: dict[str, str],
) -> dict[str, Any] | None:
    row_obj = {str(k): str(v) if v is not None else "" for k, v in item.items()}
    ih = _item_hash(row_obj, sheet_gid)
    conn = get_connection()
    gid = int(sheet_gid)
    payload = json.dumps(row_obj, ensure_ascii=False)
    ex = conn.execute(
        """
        SELECT id, created_at FROM worker_glossary_saves
        WHERE workspace_id = ? AND staff_account_id = ? AND kind = ? AND item_hash = ?
        LIMIT 1
        """,
        (workspace_id, staff_account_id, kind, ih),
    ).fetchone()
    if ex:
        return {
            "id": str(ex["id"]),
            "sheet_gid": gid,
            "item": row_obj,
            "created_at": float(ex["created_at"]),
            "already_saved": True,
        }
    sid = str(uuid.uuid4())
    now = time.time()
    try:
        conn.execute(
            """
            INSERT INTO worker_glossary_saves (id, workspace_id, staff_account_id, kind, sheet_gid, item_json, item_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (sid, workspace_id, staff_account_id, kind, gid, payload, ih, now),
        )
        conn.commit()
    except Exception:
        return None
    return {"id": sid, "sheet_gid": gid, "item": row_obj, "created_at": now, "already_saved": False}


def delete_save(workspace_id: str, staff_account_id: str, save_id: str) -> bool:
    conn = get_connection()
    cur = conn.execute(
        """
        DELETE FROM worker_glossary_saves
        WHERE id = ? AND workspace_id = ? AND staff_account_id = ?
        """,
        (save_id, workspace_id, staff_account_id),
    )
    conn.commit()
    return cur.rowcount > 0


class WorkerGlossarySaves:
    list = staticmethod(list_saves)
    add = staticmethod(add_save)
    delete = staticmethod(delete_save)


worker_glossary_saves = WorkerGlossarySaves()
