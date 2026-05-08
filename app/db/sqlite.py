"""標準 sqlite3 のみ（追加 pip 不要）。将来 PostgreSQL 等は別ドライバで拡張可能。"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from app.core.config import get_settings

_local = threading.local()


def _sqlite_file_path() -> Path:
    url = get_settings().database_url
    if not url.startswith("sqlite"):
        raise NotImplementedError(
            "このビルドは sqlite:/// のみ対応です。PostgreSQL 等はドライバ追加後に対応予定。"
        )
    if ":memory:" in url:
        raise ValueError("SQLite :memory: は未対応です。")
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise ValueError("database_url は sqlite:/// で始めてください。")
    rest = url[len(prefix) :]
    p = Path(rest)
    if not p.is_absolute():
        p = Path.cwd() / p
    return p


def init_db() -> None:
    path = _sqlite_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    try:
        # WAL モードで読み書きの並列性を大幅に上げる（複数の読みと 1 書きが同時に走れる）。
        # synchronous=NORMAL は WAL 推奨設定で、性能と耐久性のバランスがとれる。
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workspaces (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at REAL NOT NULL,
                company_name TEXT NOT NULL DEFAULT '',
                branch_name TEXT NOT NULL DEFAULT '',
                department_name TEXT NOT NULL DEFAULT ''
            )
            """
        )
        cur = conn.execute("PRAGMA table_info(workspaces)")
        existing = {row[1] for row in cur.fetchall()}
        if "admin_ui_locale" not in existing:
            conn.execute(
                "ALTER TABLE workspaces ADD COLUMN admin_ui_locale TEXT NOT NULL DEFAULT 'ja'"
            )
        cur_w = conn.execute("PRAGMA table_info(workspaces)")
        wcols = {row[1] for row in cur_w.fetchall()}
        if "admin_avatar_color_index" not in wcols:
            conn.execute(
                "ALTER TABLE workspaces ADD COLUMN admin_avatar_color_index INTEGER NOT NULL DEFAULT 0"
            )
        cur_w2 = conn.execute("PRAGMA table_info(workspaces)")
        wcols2 = {row[1] for row in cur_w2.fetchall()}
        if "admin_avatar_updated_at" not in wcols2:
            conn.execute("ALTER TABLE workspaces ADD COLUMN admin_avatar_updated_at REAL")
        cur_w3 = conn.execute("PRAGMA table_info(workspaces)")
        wcols3 = {row[1] for row in cur_w3.fetchall()}
        if "sort_order" not in wcols3:
            conn.execute(
                "ALTER TABLE workspaces ADD COLUMN sort_order REAL NOT NULL DEFAULT 0"
            )
            rows_so = conn.execute(
                "SELECT id FROM workspaces ORDER BY created_at ASC"
            ).fetchall()
            for i, rw in enumerate(rows_so):
                conn.execute(
                    "UPDATE workspaces SET sort_order = ? WHERE id = ?",
                    (float(i), rw[0]),
                )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workspace_staff_accounts (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                login_id TEXT NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                password_hash TEXT NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE(workspace_id, login_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_staff_acct_ws ON workspace_staff_accounts(workspace_id)"
        )
        cur2 = conn.execute("PRAGMA table_info(workspace_staff_accounts)")
        acct_cols = {row[1] for row in cur2.fetchall()}
        if "group_id" not in acct_cols:
            conn.execute("ALTER TABLE workspace_staff_accounts ADD COLUMN group_id TEXT")
        cur_ac2 = conn.execute("PRAGMA table_info(workspace_staff_accounts)")
        acct_cols2 = {row[1] for row in cur_ac2.fetchall()}
        if "profile_phone" not in acct_cols2:
            conn.execute(
                "ALTER TABLE workspace_staff_accounts ADD COLUMN profile_phone TEXT NOT NULL DEFAULT ''"
            )
        if "profile_email" not in acct_cols2:
            conn.execute(
                "ALTER TABLE workspace_staff_accounts ADD COLUMN profile_email TEXT NOT NULL DEFAULT ''"
            )
        if "avatar_color_index" not in acct_cols2:
            conn.execute(
                "ALTER TABLE workspace_staff_accounts ADD COLUMN avatar_color_index INTEGER NOT NULL DEFAULT 0"
            )
        if "avatar_updated_at" not in acct_cols2:
            conn.execute("ALTER TABLE workspace_staff_accounts ADD COLUMN avatar_updated_at REAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS staff_groups (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                name TEXT NOT NULL,
                sort_order REAL NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_staff_groups_ws ON staff_groups(workspace_id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS instruction_rounds (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                text TEXT NOT NULL,
                mode TEXT NOT NULL,
                target_group_id TEXT,
                created_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_inst_rounds_ws_time ON instruction_rounds(workspace_id, created_at DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS instruction_recipients (
                instruction_id TEXT NOT NULL,
                worker_token TEXT NOT NULL,
                worker_label TEXT NOT NULL DEFAULT '',
                staff_account_id TEXT,
                PRIMARY KEY (instruction_id, worker_token),
                FOREIGN KEY (instruction_id) REFERENCES instruction_rounds(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_inst_rec_inst ON instruction_recipients(instruction_id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS instruction_replies (
                instruction_id TEXT NOT NULL,
                worker_token TEXT NOT NULL,
                button TEXT NOT NULL,
                worker_label TEXT NOT NULL DEFAULT '',
                staff_account_id TEXT,
                responded_at REAL NOT NULL,
                PRIMARY KEY (instruction_id, worker_token),
                FOREIGN KEY (instruction_id) REFERENCES instruction_rounds(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ws_presence (
                workspace_id TEXT NOT NULL,
                session_token TEXT NOT NULL,
                role TEXT NOT NULL,
                user_label TEXT NOT NULL DEFAULT '',
                staff_account_id TEXT,
                connected_at REAL NOT NULL,
                last_seen_at REAL NOT NULL,
                PRIMARY KEY (workspace_id, session_token)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ws_presence_ws_role_seen ON ws_presence(workspace_id, role, last_seen_at DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workspace_chat_messages (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                from_role TEXT NOT NULL,
                worker_session_token TEXT,
                staff_account_id TEXT,
                from_label TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_workspace_chat_ws_time ON workspace_chat_messages(workspace_id, created_at DESC)"
        )
        # 翻訳結果の永続キャッシュ — 同じ日本語原文 + 言語の組み合わせは API を再呼び出ししない。
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS translation_cache (
                source_text TEXT NOT NULL,
                target_locale TEXT NOT NULL,
                translated_text TEXT NOT NULL,
                created_at REAL NOT NULL,
                last_used_at REAL NOT NULL,
                hit_count INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (source_text, target_locale)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_translation_cache_last_used ON translation_cache(last_used_at)"
        )
        # やさしい日本語キャッシュ — 用語シートが変わるたびに glossary_version が変わる。
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS easy_ja_cache (
                source_text TEXT NOT NULL,
                glossary_version TEXT NOT NULL,
                easy_text TEXT NOT NULL,
                created_at REAL NOT NULL,
                last_used_at REAL NOT NULL,
                hit_count INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (source_text, glossary_version)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_easy_ja_cache_last_used ON easy_ja_cache(last_used_at)"
        )
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")
        _cutoff = time.time() - 60 * 24 * 60 * 60
        conn.execute("DELETE FROM instruction_rounds WHERE created_at < ?", (_cutoff,))
        conn.execute("DELETE FROM ws_presence WHERE last_seen_at < ?", (time.time() - 60 * 60 * 24,))
        conn.commit()
    finally:
        conn.close()


def _ensure_staff_accounts_group_id(conn: sqlite3.Connection) -> None:
    """init_db 前に接続された古い DB など、欠落カラムを接続時に修復する。"""
    try:
        cur = conn.execute("PRAGMA table_info(workspace_staff_accounts)")
    except sqlite3.OperationalError:
        return
    cols = {row[1] for row in cur.fetchall()}
    if not cols:
        return
    altered = False
    if "group_id" not in cols:
        conn.execute("ALTER TABLE workspace_staff_accounts ADD COLUMN group_id TEXT")
        altered = True
    cols = {row[1] for row in conn.execute("PRAGMA table_info(workspace_staff_accounts)").fetchall()}
    if "profile_phone" not in cols:
        conn.execute(
            "ALTER TABLE workspace_staff_accounts ADD COLUMN profile_phone TEXT NOT NULL DEFAULT ''"
        )
        altered = True
    cols = {row[1] for row in conn.execute("PRAGMA table_info(workspace_staff_accounts)").fetchall()}
    if "profile_email" not in cols:
        conn.execute(
            "ALTER TABLE workspace_staff_accounts ADD COLUMN profile_email TEXT NOT NULL DEFAULT ''"
        )
        altered = True
    cols = {row[1] for row in conn.execute("PRAGMA table_info(workspace_staff_accounts)").fetchall()}
    if "avatar_color_index" not in cols:
        conn.execute(
            "ALTER TABLE workspace_staff_accounts ADD COLUMN avatar_color_index INTEGER NOT NULL DEFAULT 0"
        )
        altered = True
    cols = {row[1] for row in conn.execute("PRAGMA table_info(workspace_staff_accounts)").fetchall()}
    if "avatar_updated_at" not in cols:
        conn.execute("ALTER TABLE workspace_staff_accounts ADD COLUMN avatar_updated_at REAL")
        altered = True
    if altered:
        conn.commit()


def get_connection() -> sqlite3.Connection:
    """スレッドごとに接続を分離（Uvicorn ワーカー内）。"""
    if not getattr(_local, "conn", None):
        path = _sqlite_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _local.conn = sqlite3.connect(path, check_same_thread=False, timeout=30.0)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA foreign_keys = ON")
        try:
            _local.conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        try:
            _local.conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.OperationalError:
            pass
        try:
            _local.conn.execute("PRAGMA busy_timeout=8000")
        except sqlite3.OperationalError:
            pass
        _ensure_staff_accounts_group_id(_local.conn)
        _ensure_instruction_replies_custom_text(_local.conn)
        _ensure_workspace_chat_messages(_local.conn)
        _ensure_worker_glossary_saves(_local.conn)
        _ensure_workspace_glossary_terms(_local.conn)
        _ensure_workspace_expression_terms(_local.conn)
        _ensure_instruction_rounds_image_url(_local.conn)
    return _local.conn


def _ensure_workspace_chat_messages(conn: sqlite3.Connection) -> None:
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='workspace_chat_messages'"
        )
        if not cur.fetchone():
            conn.execute(
                """
                CREATE TABLE workspace_chat_messages (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    from_role TEXT NOT NULL,
                    worker_session_token TEXT,
                    staff_account_id TEXT,
                    from_label TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_workspace_chat_ws_time ON workspace_chat_messages(workspace_id, created_at DESC)"
            )
            conn.commit()
    except sqlite3.OperationalError:
        return


def _ensure_workspace_glossary_terms(conn: sqlite3.Connection) -> None:
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='workspace_glossary_terms'"
        )
        if not cur.fetchone():
            conn.execute(
                """
                CREATE TABLE workspace_glossary_terms (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    sheet_gid INTEGER NOT NULL,
                    word_ja TEXT NOT NULL,
                    meaning_ja TEXT NOT NULL,
                    note_ja TEXT NOT NULL DEFAULT '',
                    word_norm TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE (workspace_id, sheet_gid, word_norm)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_wgt_ws_sheet ON workspace_glossary_terms(workspace_id, sheet_gid)"
            )
            conn.commit()
    except sqlite3.OperationalError:
        return


def _ensure_workspace_expression_terms(conn: sqlite3.Connection) -> None:
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='workspace_expression_terms'"
        )
        if not cur.fetchone():
            conn.execute(
                """
                CREATE TABLE workspace_expression_terms (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    sheet_gid INTEGER NOT NULL,
                    phrase_ja TEXT NOT NULL,
                    meaning_ja TEXT NOT NULL,
                    note_ja TEXT NOT NULL DEFAULT '',
                    phrase_norm TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE (workspace_id, sheet_gid, phrase_norm)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_wet_ws_sheet ON workspace_expression_terms(workspace_id, sheet_gid)"
            )
            conn.commit()
    except sqlite3.OperationalError:
        return


def _ensure_instruction_rounds_image_url(conn: sqlite3.Connection) -> None:
    try:
        cur = conn.execute("PRAGMA table_info(instruction_rounds)")
        cols = {row[1] for row in cur.fetchall()}
        if cols and "image_url" not in cols:
            conn.execute("ALTER TABLE instruction_rounds ADD COLUMN image_url TEXT")
            conn.commit()
    except sqlite3.OperationalError:
        return


def _ensure_worker_glossary_saves(conn: sqlite3.Connection) -> None:
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='worker_glossary_saves'"
        )
        if not cur.fetchone():
            conn.execute(
                """
                CREATE TABLE worker_glossary_saves (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    staff_account_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    sheet_gid INTEGER NOT NULL DEFAULT 0,
                    item_json TEXT NOT NULL,
                    item_hash TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE (staff_account_id, kind, item_hash)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_wg_save_ws_staff ON worker_glossary_saves(workspace_id, staff_account_id, kind)"
            )
            conn.commit()
    except sqlite3.OperationalError:
        return


def _ensure_instruction_replies_custom_text(conn: sqlite3.Connection) -> None:
    try:
        cur = conn.execute("PRAGMA table_info(instruction_replies)")
    except sqlite3.OperationalError:
        return
    cols = {row[1] for row in cur.fetchall()}
    if not cols:
        return
    if "custom_text" not in cols:
        conn.execute("ALTER TABLE instruction_replies ADD COLUMN custom_text TEXT")
        conn.commit()
