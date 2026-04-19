"""管理者が登録する現場向け「表現」（SQLite のみ・Google シートは変更しない）。"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from app.core.config import Settings
from app.db.sqlite import get_connection
from app.services.workspace_glossary_terms import (
    existing_headwords_from_sheet,
    normalize_term,
    row_for_worker,
)


@dataclass
class WorkspaceExpressionRow:
    phrase_ja: str
    meaning_ja: str
    note_ja: str


class WorkspaceExpressionTerms:
    def list_for_sheet(self, workspace_id: str, sheet_gid: int) -> list[WorkspaceExpressionRow]:
        conn = get_connection()
        cur = conn.execute(
            """
            SELECT phrase_ja, meaning_ja, note_ja FROM workspace_expression_terms
            WHERE workspace_id = ? AND sheet_gid = ?
            ORDER BY created_at ASC
            """,
            (workspace_id, int(sheet_gid)),
        )
        return [WorkspaceExpressionRow(r[0], r[1], r[2] or "") for r in cur.fetchall()]

    def add(
        self,
        workspace_id: str,
        sheet_gid: int,
        phrase_ja: str,
        meaning_ja: str,
        note_ja: str,
        settings: Settings,
    ) -> dict[str, str]:
        w = (phrase_ja or "").strip()
        m = (meaning_ja or "").strip()
        n = (note_ja or "").strip()
        if not w or not m:
            raise ValueError("phrase_and_meaning_required")
        norm = normalize_term(w)
        if not norm:
            raise ValueError("phrase_empty")

        sheet_heads = existing_headwords_from_sheet(settings, int(sheet_gid))
        if norm in sheet_heads:
            raise ValueError("duplicate_sheet")

        conn = get_connection()
        cur = conn.execute(
            """
            SELECT id FROM workspace_expression_terms
            WHERE workspace_id = ? AND sheet_gid = ? AND phrase_norm = ?
            """,
            (workspace_id, int(sheet_gid), norm),
        )
        if cur.fetchone():
            raise ValueError("duplicate_workspace")

        cur = conn.execute(
            """
            SELECT id FROM workspace_glossary_terms
            WHERE workspace_id = ? AND sheet_gid = ? AND word_norm = ?
            """,
            (workspace_id, int(sheet_gid), norm),
        )
        if cur.fetchone():
            raise ValueError("duplicate_glossary_workspace")

        tid = str(uuid.uuid4())
        now = time.time()
        try:
            conn.execute(
                """
                INSERT INTO workspace_expression_terms
                (id, workspace_id, sheet_gid, phrase_ja, meaning_ja, note_ja, phrase_norm, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (tid, workspace_id, int(sheet_gid), w, m, n, norm, now),
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            if "UNIQUE constraint failed" in str(exc):
                raise ValueError("duplicate_workspace") from exc
            raise

        return row_for_worker(w, m, n)


workspace_expression_terms = WorkspaceExpressionTerms()
