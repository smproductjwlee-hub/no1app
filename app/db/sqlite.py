"""標準 sqlite3 のみ（追加 pip 不要）。将来 PostgreSQL 等は別ドライバで拡張可能。"""

from __future__ import annotations

import sqlite3
import threading
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
    return _local.conn
