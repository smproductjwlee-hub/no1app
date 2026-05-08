"""DB ディスパッチャ:
- DATABASE_URL が `sqlite:///...` ならローカル SQLite を使う（既存ロジック）。
- DATABASE_URL が `postgresql://...` (or `postgres://...`) なら psycopg + connection pool を使う。

呼び出し側は `get_connection()` / `init_db()` を変更なしで使える。
SQL は `?` プレースホルダのまま書く（Postgres 経路でランタイムに `%s` へ翻訳）。
スキーマの CREATE TABLE は両エンジンに通る型（TEXT, INTEGER, REAL, DOUBLE PRECISION）で書く。
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional, Sequence

from app.core.config import get_settings

_local = threading.local()


# --------- driver detection ---------


def _database_url() -> str:
    return get_settings().database_url


def _is_postgres_url(url: str) -> bool:
    return url.startswith(("postgresql://", "postgres://"))


def _is_sqlite_url(url: str) -> bool:
    return url.startswith("sqlite:")


def _driver_kind() -> str:
    url = _database_url()
    if _is_postgres_url(url):
        return "postgres"
    if _is_sqlite_url(url):
        return "sqlite"
    raise ValueError(f"Unsupported DATABASE_URL: {url}")


# --------- Postgres path ---------


_pg_pool = None
_pg_pool_lock = threading.Lock()
_PARAM_QMARK_RE = re.compile(r"\?")


def _translate_qmark_to_pg(sql: str) -> str:
    """`?` → `%s` placeholder translation. Code uses `?` everywhere; here we adapt for psycopg.
    Our SQL never contains literal `?` characters in strings, so a global replace is safe.
    """
    return _PARAM_QMARK_RE.sub("%s", sql)


class _HybridRow:
    """sqlite3.Row 互換の行: row[0] / row['col'] どちらでもアクセス可能。"""

    __slots__ = ("_cols", "_vals", "_index")

    def __init__(self, cols: Sequence[str], vals: Sequence[Any]) -> None:
        self._cols = list(cols)
        self._vals = list(vals)
        self._index = {c: i for i, c in enumerate(self._cols)}

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._vals[key]
        return self._vals[self._index[key]]

    def get(self, key, default=None):
        i = self._index.get(key)
        return default if i is None else self._vals[i]

    def keys(self):
        return list(self._cols)

    def __iter__(self):
        return iter(self._vals)

    def __len__(self):
        return len(self._vals)

    def __contains__(self, key):
        return key in self._index


def _pg_row_factory(cursor):
    cols = [d.name for d in cursor.description] if cursor.description else []
    def make(values):
        return _HybridRow(cols, values)
    return make


class _PgCursorAdapter:
    def __init__(self, cur) -> None:
        self._cur = cur

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def lastrowid(self):
        # 互換のため。Postgres では SERIAL / IDENTITY 等を使うべき。
        return None

    def close(self):
        try:
            self._cur.close()
        except Exception:
            pass


class _PgConnAdapter:
    """sqlite3.Connection 互換 API: execute / executemany / commit / rollback。
    SQL は `?` で渡してよい（内部で `%s` に翻訳）。
    フェッチ結果は _HybridRow で sqlite3.Row 同様に利用可。
    """

    def __init__(self, conn) -> None:
        self._conn = conn

    def execute(self, sql: str, params: Optional[Sequence[Any]] = ()) -> _PgCursorAdapter:
        translated = _translate_qmark_to_pg(sql)
        cur = self._conn.execute(translated, tuple(params) if params else ())
        return _PgCursorAdapter(cur)

    def executemany(self, sql: str, seq_of_params) -> _PgCursorAdapter:
        translated = _translate_qmark_to_pg(sql)
        cur = self._conn.cursor()
        cur.executemany(translated, list(seq_of_params))
        return _PgCursorAdapter(cur)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        # スレッド寿命中は接続をプールに戻さない（次の呼び出しで再利用）。
        pass


def _build_pg_pool():
    import psycopg
    from psycopg_pool import ConnectionPool
    url = _database_url()
    # ログに出ても安全なように password を伏せる
    safe_url = re.sub(r":[^:@/]+@", ":***@", url)
    # スモークテスト: プール構築前に実際の接続を試して、本当のエラーを表面化させる
    # （PoolTimeout が本当の原因をマスクしてしまうのを避ける）
    print(f"[db] testing Postgres connection to: {safe_url}")
    try:
        with psycopg.connect(url, connect_timeout=10) as test_conn:
            test_conn.execute("SELECT 1").fetchone()
        print("[db] Postgres smoke test OK")
    except Exception as e:
        raise RuntimeError(
            f"Cannot connect to Postgres ({safe_url}): {type(e).__name__}: {e}"
        ) from e
    # Supabase Pooler (port 6543, mode=transaction) を推奨。
    # min_size=1 で起動を速く（後で必要に応じて増える）。
    return ConnectionPool(
        url,
        min_size=1,
        max_size=int(os.environ.get("DB_POOL_MAX_SIZE", "16")),
        timeout=30,
        max_lifetime=3600,
        kwargs={"row_factory": _pg_row_factory},
    )


def _get_pg_pool():
    global _pg_pool
    if _pg_pool is not None:
        return _pg_pool
    with _pg_pool_lock:
        if _pg_pool is None:
            _pg_pool = _build_pg_pool()
    return _pg_pool


def _init_db_pg() -> None:
    """Postgres 経路: 初回起動時にすべてのテーブル / インデックスを冪等に作成する。
    SQLite と違い、過去の旧スキーマからの逐次 ALTER 移行は不要（新規 DB を前提とする）。
    """
    pool = _get_pg_pool()
    with pool.connection() as conn:
        # workspaces（インクリメンタル ALTER 後の最終形）
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workspaces (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at DOUBLE PRECISION NOT NULL,
                company_name TEXT NOT NULL DEFAULT '',
                branch_name TEXT NOT NULL DEFAULT '',
                department_name TEXT NOT NULL DEFAULT '',
                admin_ui_locale TEXT NOT NULL DEFAULT 'ja',
                admin_avatar_color_index INTEGER NOT NULL DEFAULT 0,
                admin_avatar_updated_at DOUBLE PRECISION,
                sort_order DOUBLE PRECISION NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workspace_staff_accounts (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                login_id TEXT NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                password_hash TEXT NOT NULL,
                created_at DOUBLE PRECISION NOT NULL,
                group_id TEXT,
                profile_phone TEXT NOT NULL DEFAULT '',
                profile_email TEXT NOT NULL DEFAULT '',
                avatar_color_index INTEGER NOT NULL DEFAULT 0,
                avatar_updated_at DOUBLE PRECISION,
                UNIQUE(workspace_id, login_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_wsa_workspace ON workspace_staff_accounts(workspace_id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS staff_groups (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                name TEXT NOT NULL,
                created_at DOUBLE PRECISION NOT NULL,
                UNIQUE(workspace_id, name)
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
                created_at DOUBLE PRECISION NOT NULL,
                image_url TEXT
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
                responded_at DOUBLE PRECISION NOT NULL,
                custom_text TEXT,
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
                connected_at DOUBLE PRECISION NOT NULL,
                last_seen_at DOUBLE PRECISION NOT NULL,
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
                created_at DOUBLE PRECISION NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_workspace_chat_ws_time ON workspace_chat_messages(workspace_id, created_at DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workspace_glossary_terms (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                sheet_gid INTEGER NOT NULL,
                word_ja TEXT NOT NULL,
                meaning_ja TEXT NOT NULL,
                note_ja TEXT NOT NULL DEFAULT '',
                word_norm TEXT NOT NULL,
                created_at DOUBLE PRECISION NOT NULL,
                UNIQUE (workspace_id, sheet_gid, word_norm)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wgt_ws_sheet ON workspace_glossary_terms(workspace_id, sheet_gid)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workspace_expression_terms (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                sheet_gid INTEGER NOT NULL,
                phrase_ja TEXT NOT NULL,
                meaning_ja TEXT NOT NULL,
                note_ja TEXT NOT NULL DEFAULT '',
                phrase_norm TEXT NOT NULL,
                created_at DOUBLE PRECISION NOT NULL,
                UNIQUE (workspace_id, sheet_gid, phrase_norm)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wet_ws_sheet ON workspace_expression_terms(workspace_id, sheet_gid)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS worker_glossary_saves (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                staff_account_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                sheet_gid INTEGER NOT NULL DEFAULT 0,
                item_json TEXT NOT NULL,
                item_hash TEXT NOT NULL,
                created_at DOUBLE PRECISION NOT NULL,
                UNIQUE (staff_account_id, kind, item_hash)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wg_save_ws_staff ON worker_glossary_saves(workspace_id, staff_account_id, kind)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS translation_cache (
                source_text TEXT NOT NULL,
                target_locale TEXT NOT NULL,
                translated_text TEXT NOT NULL,
                created_at DOUBLE PRECISION NOT NULL,
                last_used_at DOUBLE PRECISION NOT NULL,
                hit_count INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (source_text, target_locale)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_translation_cache_last_used ON translation_cache(last_used_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS easy_ja_cache (
                source_text TEXT NOT NULL,
                glossary_version TEXT NOT NULL,
                easy_text TEXT NOT NULL,
                created_at DOUBLE PRECISION NOT NULL,
                last_used_at DOUBLE PRECISION NOT NULL,
                hit_count INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (source_text, glossary_version)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_easy_ja_cache_last_used ON easy_ja_cache(last_used_at)"
        )
        # 古いデータの整理（60日以上前の指示・1日以上前の presence）
        cutoff = time.time() - 60 * 24 * 60 * 60
        conn.execute("DELETE FROM instruction_rounds WHERE created_at < %s", (cutoff,))
        conn.execute("DELETE FROM ws_presence WHERE last_seen_at < %s", (time.time() - 60 * 60 * 24,))
        conn.commit()


def _get_connection_pg():
    """スレッドローカルに 1 つの psycopg 接続をぶら下げる（プールから借りっぱなし）。
    asyncio.to_thread の executor は同じスレッドを使い回すので、接続は再利用される。
    プール max_size = DB_POOL_MAX_SIZE (default 16) より多い同時アクセスは待ち。
    """
    conn = getattr(_local, "pg_conn_adapter", None)
    if conn is not None:
        return conn
    pool = _get_pg_pool()
    raw = pool.getconn(timeout=30)
    # autocommit OFF（明示的 commit を期待する API 互換性のため）
    raw.autocommit = False
    adapter = _PgConnAdapter(raw)
    _local.pg_conn_adapter = adapter
    _local.pg_raw_conn = raw
    return adapter


# --------- SQLite path (既存ロジック保存) ---------


def _sqlite_file_path() -> Path:
    url = _database_url()
    if not _is_sqlite_url(url):
        raise NotImplementedError(
            "SQLite 経路で呼ばれましたが DATABASE_URL は sqlite:/// ではありません。"
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


# --------- 公開 API: ドライバ分岐 ---------


def init_db() -> None:
    if _driver_kind() == "postgres":
        _init_db_pg()
    else:
        _init_db_sqlite()


def get_connection():
    if _driver_kind() == "postgres":
        return _get_connection_pg()
    return _get_connection_sqlite()


# --------- SQLite: 元の init_db / get_connection 本体（リネーム保存） ---------


def _init_db_sqlite() -> None:
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


def _get_connection_sqlite() -> sqlite3.Connection:
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
