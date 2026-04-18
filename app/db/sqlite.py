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
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")
        _cutoff = time.time() - 60 * 24 * 60 * 60
        conn.execute("DELETE FROM instruction_rounds WHERE created_at < ?", (_cutoff,))
        conn.commit()
    finally:
        conn.close()


def get_connection() -> sqlite3.Connection:
    """スレッドごとに接続を分離（Uvicorn ワーカー内）。"""
    if not getattr(_local, "conn", None):
        path = _sqlite_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _local.conn = sqlite3.connect(path, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA foreign_keys = ON")
    return _local.conn
