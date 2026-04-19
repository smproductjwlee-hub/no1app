"""管理者が登録する現場向け用語（Google シート分野タブ＋重複チェック、ワーカー用にシート行と合成）。"""

from __future__ import annotations

import re
import time
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings, get_settings
from app.db.sqlite import get_connection
from app.services.google_sheets import fetch_sheet_grid, get_sheets_service, grid_to_records

_RE_HEAD = re.compile(r"日本語|単語")


def normalize_term(s: str) -> str:
    return unicodedata.normalize("NFKC", (s or "").strip())


def pick_headword_from_row(row: dict[str, str]) -> str:
    for k, v in row.items():
        if _RE_HEAD.search(k) and v and str(v).strip():
            return str(v).strip()
    for k in row:
        v = row.get(k, "")
        if v and str(v).strip():
            return str(v).strip()
    return ""


def _fetch_food_glossary_rows(settings: Settings, sheet_gid: int) -> list[dict[str, str]]:
    service = get_sheets_service(settings)
    grid = fetch_sheet_grid(service, settings.food_glossary_spreadsheet_id, int(sheet_gid))
    return grid_to_records(grid, settings.food_glossary_header_row)


def existing_headwords_from_sheet(settings: Settings, sheet_gid: int) -> set[str]:
    try:
        rows = _fetch_food_glossary_rows(settings, sheet_gid)
    except Exception:
        return set()
    out: set[str] = set()
    for row in rows:
        w = pick_headword_from_row(row)
        if w:
            out.add(normalize_term(w))
    return out


def row_for_worker(word_ja: str, meaning_ja: str, note_ja: str) -> dict[str, str]:
    """workerGlossaryPickCols と一覧表示用（単語 / 意味 / 使用例・補足）。"""
    return {
        "単語・表現": word_ja.strip(),
        "意味": meaning_ja.strip(),
        "使用例": (note_ja or "").strip(),
    }


@dataclass
class WorkspaceTermRow:
    word_ja: str
    meaning_ja: str
    note_ja: str


class WorkspaceGlossaryTerms:
    def list_for_sheet(self, workspace_id: str, sheet_gid: int) -> list[WorkspaceTermRow]:
        conn = get_connection()
        cur = conn.execute(
            """
            SELECT word_ja, meaning_ja, note_ja FROM workspace_glossary_terms
            WHERE workspace_id = ? AND sheet_gid = ?
            ORDER BY created_at ASC
            """,
            (workspace_id, int(sheet_gid)),
        )
        return [WorkspaceTermRow(r[0], r[1], r[2] or "") for r in cur.fetchall()]

    def add(
        self,
        workspace_id: str,
        sheet_gid: int,
        word_ja: str,
        meaning_ja: str,
        note_ja: str,
        settings: Settings,
    ) -> dict[str, str]:
        w = (word_ja or "").strip()
        m = (meaning_ja or "").strip()
        n = (note_ja or "").strip()
        if not w or not m:
            raise ValueError("word_and_meaning_required")
        norm = normalize_term(w)
        if not norm:
            raise ValueError("word_empty")

        sheet_heads = existing_headwords_from_sheet(settings, int(sheet_gid))
        if norm in sheet_heads:
            raise ValueError("duplicate_sheet")

        conn = get_connection()
        cur = conn.execute(
            """
            SELECT id FROM workspace_glossary_terms
            WHERE workspace_id = ? AND sheet_gid = ? AND word_norm = ?
            """,
            (workspace_id, int(sheet_gid), norm),
        )
        if cur.fetchone():
            raise ValueError("duplicate_workspace")

        cur = conn.execute(
            """
            SELECT id FROM workspace_expression_terms
            WHERE workspace_id = ? AND sheet_gid = ? AND phrase_norm = ?
            """,
            (workspace_id, int(sheet_gid), norm),
        )
        if cur.fetchone():
            raise ValueError("duplicate_expression_workspace")

        tid = str(uuid.uuid4())
        now = time.time()
        try:
            conn.execute(
                """
                INSERT INTO workspace_glossary_terms
                (id, workspace_id, sheet_gid, word_ja, meaning_ja, note_ja, word_norm, created_at)
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

    def merged_food_glossary(
        self,
        workspace_id: str,
        settings: Settings,
        sheet_gid: int | None,
    ) -> dict[str, Any]:
        gid = int(settings.food_glossary_sheet_gid if sheet_gid is None else sheet_gid)
        base_rows: list[dict[str, str]] = []
        try:
            base_rows = _fetch_food_glossary_rows(settings, gid)
        except Exception:
            base_rows = []
        custom = self.list_for_sheet(workspace_id, gid)
        extra = [row_for_worker(t.word_ja, t.meaning_ja, t.note_ja) for t in custom]
        from app.services.workspace_expression_terms import workspace_expression_terms

        expr = workspace_expression_terms.list_for_sheet(workspace_id, gid)
        extra_e = [row_for_worker(t.phrase_ja, t.meaning_ja, t.note_ja) for t in expr]
        rows = base_rows + extra + extra_e
        return {
            "spreadsheet_id": settings.food_glossary_spreadsheet_id,
            "sheet_gid": gid,
            "header_row": settings.food_glossary_header_row,
            "row_count": len(rows),
            "rows": rows,
        }


workspace_glossary_terms = WorkspaceGlossaryTerms()
