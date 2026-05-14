"""ワークスペースは SQLite ファイルで永続化。その他はセッション用メモリ。"""

from __future__ import annotations

import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from app.db.sqlite import get_connection, is_unique_violation, make_slug_from_name
from app.services.staff_avatar_files import delete_admin_file


# 総運営スーパー管理者セッション用（WS 接続不可・顧客ワークスペース選択のみ）
SUPER_WORKSPACE_ID = "__wb_super__"


class Role(str, Enum):
    ADMIN = "admin"
    WORKER = "worker"
    SUPER_ADMIN = "super_admin"
    # Phase 2.x — 3계층 멀티테넌시の中間層: 대리점 관리자
    DISTRIBUTOR_ADMIN = "distributor_admin"


@dataclass
class Workspace:
    id: str
    name: str
    created_at: float = field(default_factory=time.time)
    # 総運営一覧の並び順（小さいほど上）
    sort_order: float = 0.0
    # 現場メタ（管理者が編集、総運営一覧でも参照）
    company_name: str = ""
    branch_name: str = ""
    department_name: str = ""
    # 管理者 UI 言語: ja en ko zh vi id（ブラウザ音声認識が弱い言語は除外）
    admin_ui_locale: str = "ja"
    admin_avatar_color_index: int = 0
    admin_avatar_updated_at: Optional[float] = None
    # Phase 1.5: 請求 / 代理店分配 (旧 — Phase 2.x で distributor_id / 도매·소매가로 이행 중)
    distributor_name: str = ""        # legacy: 代理店名 (例: "PoPo"). 空なら直販
    monthly_price_jpy: int = 0         # legacy: 月額利用料 (Phase 2.x で retail_price_* に이행)
    commission_rate_pct: int = 20      # legacy: 手数料率 (Phase 2.x 도매 모델에서 미사용)
    billing_start_at: Optional[float] = None  # 課金開始日(UNIX秒). 月割り計算で使う
    # Phase 2.x: 3계층 멀티테넌시 + 도매·소매가
    distributor_id: str = ""           # 소속 대리점 ID (c-direct = 직판)
    slug: str = ""                     # URL 슬러그 (distributor_id 내 unique)
    logo_url: Optional[str] = None     # 좌상단 표시용 로고 URL
    primary_color: Optional[str] = None  # 옵션: 테마 색
    owner_password_hash: str = ""      # 점주 로그인용 (대리점이 발급)
    force_password_change_on_login: bool = False
    retail_price_starter: Optional[int] = None    # 대리점이 정한 소매가 (영업 비밀)
    retail_price_business: Optional[int] = None
    retail_price_enterprise: Optional[int] = None
    assigned_plan: str = "starter"     # starter / business / enterprise — API 초과시 자동 업그레이드


@dataclass
class Session:
    token: str
    workspace_id: str
    role: Role
    user_label: Optional[str]
    expires_at: float
    # 個人アカウントログイン時のみ（グループ送信・一覧用）
    staff_account_id: Optional[str] = None
    # Phase 2.x: 3계층 멀티테넌시 — 소속 대리점 ID
    # super_admin → "" (모든 대리점에 권한)
    # distributor_admin → 자기 대리점 id (워크스페이스 미보유)
    # admin/worker → 워크스페이스의 distributor_id (격리 검증용)
    distributor_id: str = ""


def _row_to_workspace(row: sqlite3.Row) -> Workspace:
    keys = row.keys()
    loc = "ja"
    if "admin_ui_locale" in keys and row["admin_ui_locale"]:
        loc = str(row["admin_ui_locale"]).strip() or "ja"
    aci = 0
    if "admin_avatar_color_index" in keys and row["admin_avatar_color_index"] is not None:
        try:
            aci = int(row["admin_avatar_color_index"]) % 8
        except (TypeError, ValueError):
            aci = 0
    av_ad = None
    if "admin_avatar_updated_at" in keys and row["admin_avatar_updated_at"] is not None:
        try:
            av_ad = float(row["admin_avatar_updated_at"])
        except (TypeError, ValueError):
            av_ad = None
    so = 0.0
    if "sort_order" in keys and row["sort_order"] is not None:
        try:
            so = float(row["sort_order"])
        except (TypeError, ValueError):
            so = 0.0
    # Phase 1.5 billing fields
    dist = ""
    if "distributor_name" in keys and row["distributor_name"]:
        dist = str(row["distributor_name"]).strip()
    mprice = 0
    if "monthly_price_jpy" in keys and row["monthly_price_jpy"] is not None:
        try:
            mprice = int(row["monthly_price_jpy"])
        except (TypeError, ValueError):
            mprice = 0
    crate = 20
    if "commission_rate_pct" in keys and row["commission_rate_pct"] is not None:
        try:
            crate = max(0, min(100, int(row["commission_rate_pct"])))
        except (TypeError, ValueError):
            crate = 20
    bstart = None
    if "billing_start_at" in keys and row["billing_start_at"] is not None:
        try:
            bstart = float(row["billing_start_at"])
        except (TypeError, ValueError):
            bstart = None
    # Phase 2.x: 3계층 멀티테넌시 + 도매·소매가
    dist_id = ""
    if "distributor_id" in keys and row["distributor_id"]:
        dist_id = str(row["distributor_id"]).strip()
    slug = ""
    if "slug" in keys and row["slug"]:
        slug = str(row["slug"]).strip()
    logo_url = None
    if "logo_url" in keys and row["logo_url"]:
        logo_url = str(row["logo_url"]).strip() or None
    primary_color = None
    if "primary_color" in keys and row["primary_color"]:
        primary_color = str(row["primary_color"]).strip() or None
    owner_ph = ""
    if "owner_password_hash" in keys and row["owner_password_hash"]:
        owner_ph = str(row["owner_password_hash"])
    force_pw = False
    if "force_password_change_on_login" in keys and row["force_password_change_on_login"] is not None:
        try:
            force_pw = bool(int(row["force_password_change_on_login"]))
        except (TypeError, ValueError):
            force_pw = False
    def _ri(key):  # 안전한 int (NULL OK)
        if key in keys and row[key] is not None:
            try:
                return int(row[key])
            except (TypeError, ValueError):
                return None
        return None
    plan = "starter"
    if "assigned_plan" in keys and row["assigned_plan"]:
        v = str(row["assigned_plan"]).strip().lower()
        if v in ("starter", "business", "enterprise"):
            plan = v
    return Workspace(
        id=row["id"],
        name=row["name"],
        created_at=float(row["created_at"]),
        sort_order=so,
        company_name=row["company_name"] or "",
        branch_name=row["branch_name"] or "",
        department_name=row["department_name"] or "",
        admin_ui_locale=loc,
        admin_avatar_color_index=aci,
        admin_avatar_updated_at=av_ad,
        distributor_name=dist,
        monthly_price_jpy=mprice,
        commission_rate_pct=crate,
        billing_start_at=bstart,
        distributor_id=dist_id,
        slug=slug,
        logo_url=logo_url,
        primary_color=primary_color,
        owner_password_hash=owner_ph,
        force_password_change_on_login=force_pw,
        retail_price_starter=_ri("retail_price_starter"),
        retail_price_business=_ri("retail_price_business"),
        retail_price_enterprise=_ri("retail_price_enterprise"),
        assigned_plan=plan,
    )


class WorkspaceStore:
    def create(
        self,
        name: str,
        *,
        distributor_id: Optional[str] = None,
        slug: Optional[str] = None,
        owner_password_hash: str = "",
        logo_url: Optional[str] = None,
        primary_color: Optional[str] = None,
        retail_price_starter: Optional[int] = None,
        retail_price_business: Optional[int] = None,
        retail_price_enterprise: Optional[int] = None,
        assigned_plan: str = "starter",
        force_password_change_on_login: bool = False,
        company_name: str = "",
    ) -> Workspace:
        """워크스페이스 생성.

        - distributor_id 가 None 이면 'c-direct' (직판) 의 id 를 자동 lookup
        - slug 가 None 이면 name/company_name 에서 자동 생성, 충돌 시 -2, -3 suffix
        - (distributor_id, slug) 복합 유니크 인덱스로 보호됨
        """
        from app.db.sqlite import _ensure_unique_workspace_slug  # type: ignore

        conn = get_connection()
        # distributor_id 미지정시 c-direct 매핑
        if not distributor_id:
            r = conn.execute(
                "SELECT id FROM distributors WHERE slug = ?", ("c-direct",)
            ).fetchone()
            if r is None:
                raise RuntimeError("c-direct distributor not seeded; run init_db() first.")
            distributor_id = r[0] if not hasattr(r, "keys") else r["id"]

        # slug 자동 생성 또는 명시적 사용
        ws_id = str(uuid.uuid4())
        if not slug:
            # 미지정 → 자동 생성 + 충돌 시 -2, -3 suffix
            base = make_slug_from_name(company_name or name, fallback_id=ws_id)
            slug = _ensure_unique_workspace_slug(conn, distributor_id, base, ws_id)
        else:
            # 명시 입력 → 정규화만 하고 그대로 시도. 충돌 시 IntegrityError 로 거부.
            slug = make_slug_from_name(slug, fallback_id=ws_id)

        now = time.time()
        mx_row = conn.execute("SELECT COALESCE(MAX(sort_order), -1.0) FROM workspaces").fetchone()
        try:
            mx = float(mx_row[0]) if mx_row and mx_row[0] is not None else -1.0
        except (TypeError, ValueError):
            mx = -1.0
        sort_next = mx + 1.0
        assigned = (assigned_plan or "starter").strip().lower()
        if assigned not in ("starter", "business", "enterprise"):
            assigned = "starter"
        try:
            conn.execute(
                """
                INSERT INTO workspaces (
                    id, name, created_at, company_name, branch_name, department_name,
                    admin_ui_locale, sort_order,
                    distributor_id, slug, logo_url, primary_color,
                    owner_password_hash, force_password_change_on_login,
                    retail_price_starter, retail_price_business, retail_price_enterprise,
                    assigned_plan
                )
                VALUES (?, ?, ?, ?, '', '', 'ja', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ws_id, name, now, company_name or "", sort_next,
                    distributor_id, slug, logo_url, primary_color,
                    owner_password_hash, 1 if force_password_change_on_login else 0,
                    retail_price_starter, retail_price_business, retail_price_enterprise,
                    assigned,
                ),
            )
            conn.commit()
        except Exception as exc:
            if is_unique_violation(exc):
                raise ValueError(f"workspace slug '{slug}' already exists for this distributor") from exc
            raise
        r = conn.execute("SELECT * FROM workspaces WHERE id = ?", (ws_id,)).fetchone()
        assert r is not None
        return _row_to_workspace(r)

    def get(self, workspace_id: str) -> Optional[Workspace]:
        conn = get_connection()
        row = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        return _row_to_workspace(row) if row else None

    def find_by_name(self, name: str) -> Optional[Workspace]:
        """大文字小文字を区別せず名前一致で検索（legacy MVP）。

        Phase 2.x: 同名ワークスペースが複数 distributor にまたがり得るため、
        本メソッドは「c-direct (直販) スコープ内」 でのみ検索する。
        slug ベースの find_by_slugs() を新規利用には推奨。
        """
        key = name.strip().lower()
        if not key:
            return None
        conn = get_connection()
        cd = conn.execute(
            "SELECT id FROM distributors WHERE slug = ?", ("c-direct",)
        ).fetchone()
        if cd is None:
            # c-direct 미생성 (init 전) 시 legacy 동작 fallback
            row = conn.execute(
                "SELECT * FROM workspaces WHERE lower(trim(name)) = ? LIMIT 1",
                (key,),
            ).fetchone()
            return _row_to_workspace(row) if row else None
        c_direct_id = cd[0] if not hasattr(cd, "keys") else cd["id"]
        row = conn.execute(
            "SELECT * FROM workspaces WHERE lower(trim(name)) = ? AND distributor_id = ? LIMIT 1",
            (key, c_direct_id),
        ).fetchone()
        return _row_to_workspace(row) if row else None

    def find_by_slugs(self, distributor_slug: str, workspace_slug: str) -> Optional[Workspace]:
        """URL 라우팅용: (대리점 슬러그, 워크스페이스 슬러그) → Workspace.

        예: find_by_slugs("popo", "abcramen") → ABCラーメン의 Workspace
        """
        ds = (distributor_slug or "").strip().lower()
        ws = (workspace_slug or "").strip().lower()
        if not ds or not ws:
            return None
        conn = get_connection()
        row = conn.execute(
            """
            SELECT w.* FROM workspaces w
            JOIN distributors d ON d.id = w.distributor_id
            WHERE d.slug = ? AND w.slug = ?
            LIMIT 1
            """,
            (ds, ws),
        ).fetchone()
        return _row_to_workspace(row) if row else None

    def list_by_distributor(self, distributor_id: str) -> list[Workspace]:
        """대리점이 자기 산하 워크스페이스만 보는 용도."""
        if not distributor_id:
            return []
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM workspaces WHERE distributor_id = ? "
            "ORDER BY sort_order ASC, created_at ASC",
            (distributor_id,),
        ).fetchall()
        return [_row_to_workspace(r) for r in rows]

    def list_all(self) -> list[Workspace]:
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM workspaces ORDER BY sort_order ASC, created_at ASC"
        ).fetchall()
        return [_row_to_workspace(r) for r in rows]

    def reorder_super(self, ordered_ids: list[str]) -> None:
        """総運営: 全ワークスペース ID をこの順で並べる。"""
        conn = get_connection()
        db_ids = {
            str(r[0])
            for r in conn.execute("SELECT id FROM workspaces").fetchall()
        }
        want = [str(x).strip() for x in ordered_ids if str(x).strip()]
        if set(want) != db_ids or len(want) != len(db_ids):
            raise ValueError("ordered_ids must list every workspace exactly once")
        for i, wid in enumerate(want):
            conn.execute(
                "UPDATE workspaces SET sort_order = ? WHERE id = ?",
                (float(i), wid),
            )
        conn.commit()

    def update_org(
        self,
        workspace_id: str,
        *,
        company_name: Optional[str] = None,
        branch_name: Optional[str] = None,
        department_name: Optional[str] = None,
        admin_ui_locale: Optional[str] = None,
        admin_avatar_color_index: Optional[int] = None,
    ) -> Optional[Workspace]:
        conn = get_connection()
        row = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        if row is None:
            return None
        sets: list[str] = []
        vals: list = []
        if company_name is not None:
            sets.append("company_name = ?")
            vals.append(company_name.strip())
        if branch_name is not None:
            sets.append("branch_name = ?")
            vals.append(branch_name.strip())
        if department_name is not None:
            sets.append("department_name = ?")
            vals.append(department_name.strip())
        if admin_ui_locale is not None:
            sets.append("admin_ui_locale = ?")
            vals.append(admin_ui_locale.strip() or "ja")
        if admin_avatar_color_index is not None:
            sets.append("admin_avatar_color_index = ?")
            vals.append(int(admin_avatar_color_index) % 8)
        if sets:
            sql = "UPDATE workspaces SET " + ", ".join(sets) + " WHERE id = ?"
            vals.append(workspace_id)
            conn.execute(sql, vals)
            conn.commit()
        r = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        return _row_to_workspace(r) if r else None

    def update_billing(
        self,
        workspace_id: str,
        *,
        distributor_name: Any = None,
        monthly_price_jpy: Any = None,
        commission_rate_pct: Any = None,
        billing_start_at: Any = None,
    ) -> Optional[Workspace]:
        """Phase 1.5 — 代理店名・月額料金・手数料率・課金開始日の更新（None は変更なし）。"""
        conn = get_connection()
        sets: list[str] = []
        vals: list[Any] = []
        if distributor_name is not None:
            sets.append("distributor_name = ?")
            vals.append(str(distributor_name).strip())
        if monthly_price_jpy is not None:
            try:
                v = max(0, int(monthly_price_jpy))
            except (TypeError, ValueError):
                v = 0
            sets.append("monthly_price_jpy = ?")
            vals.append(v)
        if commission_rate_pct is not None:
            try:
                v = max(0, min(100, int(commission_rate_pct)))
            except (TypeError, ValueError):
                v = 20
            sets.append("commission_rate_pct = ?")
            vals.append(v)
        if billing_start_at is not None:
            # 0 / 空文字 / 'null' は NULL クリア扱い
            try:
                fv = float(billing_start_at) if billing_start_at not in ("", None, 0, "0") else None
            except (TypeError, ValueError):
                fv = None
            sets.append("billing_start_at = ?")
            vals.append(fv)
        if not sets:
            r = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
            return _row_to_workspace(r) if r else None
        vals.append(workspace_id)
        conn.execute(
            "UPDATE workspaces SET " + ", ".join(sets) + " WHERE id = ?",
            vals,
        )
        conn.commit()
        r = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        return _row_to_workspace(r) if r else None

    def set_logo_url(self, workspace_id: str, logo_url: Optional[str]) -> Optional[Workspace]:
        """Phase 2.8: 워크스페이스 로고 URL 갱신. None / 빈 문자열은 클리어."""
        url = (logo_url or "").strip() or None
        conn = get_connection()
        conn.execute(
            "UPDATE workspaces SET logo_url = ? WHERE id = ?",
            (url, workspace_id),
        )
        conn.commit()
        r = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        return _row_to_workspace(r) if r else None

    def set_admin_avatar_updated_at(self, workspace_id: str, ts: float) -> Optional[Workspace]:
        conn = get_connection()
        conn.execute(
            "UPDATE workspaces SET admin_avatar_updated_at = ? WHERE id = ?",
            (ts, workspace_id),
        )
        conn.commit()
        r = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        return _row_to_workspace(r) if r else None

    def clear_admin_avatar(self, workspace_id: str) -> Optional[Workspace]:
        delete_admin_file(workspace_id)
        conn = get_connection()
        conn.execute(
            "UPDATE workspaces SET admin_avatar_updated_at = NULL WHERE id = ?",
            (workspace_id,),
        )
        conn.commit()
        r = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        return _row_to_workspace(r) if r else None

    def delete_with_cascade(self, workspace_id: str) -> dict:
        """ワークスペースとその所有データを全て削除する（GDPR / 個人情報保護法 削除依頼対応）。

        単一トランザクションで関連テーブルを全て DELETE。失敗時は全てロールバック。
        DB トランザクション成功後、ファイルシステム上のアップロード（アバター・指示画像）も
        best-effort で削除する。

        戻り値: {table_name: deleted_row_count, "files_deleted": int}
        """
        from app.services.staff_avatar_files import delete_file as _delete_staff_avatar
        from app.services.instruction_images import delete_workspace_dir

        conn = get_connection()
        counts: dict[str, int] = {}
        # アバターファイル削除のためにスタッフ ID を先に取得
        staff_ids: list[str] = []
        try:
            rows = conn.execute(
                "SELECT id FROM workspace_staff_accounts WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchall()
            staff_ids = [r["id"] for r in rows]
            # 依存順（子 → 親）で DELETE。
            # instruction_recipients / instruction_replies は instruction_rounds の FK ON DELETE CASCADE で自動削除される。
            for table in (
                "worker_glossary_saves",
                "workspace_expression_terms",
                "workspace_glossary_terms",
                "workspace_chat_messages",
                "ws_presence",
                "instruction_rounds",
                "staff_groups",
                "workspace_staff_accounts",
            ):
                cur = conn.execute(f"DELETE FROM {table} WHERE workspace_id = ?", (workspace_id,))
                counts[table] = int(getattr(cur, "rowcount", 0) or 0)
            cur = conn.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
            counts["workspaces"] = int(getattr(cur, "rowcount", 0) or 0)
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise

        # ファイルシステム掃除（best-effort、トランザクション後）
        files_deleted = 0
        try:
            delete_admin_file(workspace_id)
            files_deleted += 1
        except Exception:
            pass
        for sid in staff_ids:
            try:
                _delete_staff_avatar(sid)
                files_deleted += 1
            except Exception:
                pass
        try:
            files_deleted += delete_workspace_dir(workspace_id)
        except Exception:
            pass
        counts["files_deleted"] = files_deleted
        return counts

    def export_full(self, workspace_id: str) -> Optional[dict]:
        """ワークスペース所有データを 1 つの JSON に書き出す（データポータビリティ要請対応）。

        個人パスワードハッシュは除外（流出時の被害最小化）。
        スタッフのアバター画像本体は含めず、URL のみ含む。
        """
        conn = get_connection()
        ws_row = conn.execute(
            "SELECT * FROM workspaces WHERE id = ?", (workspace_id,)
        ).fetchone()
        if ws_row is None:
            return None

        def _rows(table: str, key: str = "workspace_id") -> list[dict]:
            cur = conn.execute(f"SELECT * FROM {table} WHERE {key} = ?", (workspace_id,))
            cols = [d[0] for d in cur.description] if cur.description else []
            out: list[dict] = []
            for r in cur.fetchall():
                out.append({c: r[c] for c in cols if c != "password_hash"})
            return out

        staff_accounts_rows = _rows("workspace_staff_accounts")
        # instruction_rounds 経由で recipients / replies を取得
        rounds = _rows("instruction_rounds")
        rid_list = [r["id"] for r in rounds]
        recipients: list[dict] = []
        replies: list[dict] = []
        if rid_list:
            qs = ",".join(["?"] * len(rid_list))
            for r in conn.execute(
                f"SELECT * FROM instruction_recipients WHERE instruction_id IN ({qs})",
                tuple(rid_list),
            ).fetchall():
                recipients.append({k: r[k] for k in r.keys()})
            for r in conn.execute(
                f"SELECT * FROM instruction_replies WHERE instruction_id IN ({qs})",
                tuple(rid_list),
            ).fetchall():
                replies.append({k: r[k] for k in r.keys()})

        return {
            "workspace": {k: ws_row[k] for k in ws_row.keys()},
            "staff_groups": _rows("staff_groups"),
            "staff_accounts": staff_accounts_rows,
            "instruction_rounds": rounds,
            "instruction_recipients": recipients,
            "instruction_replies": replies,
            "ws_presence": _rows("ws_presence"),
            "workspace_chat_messages": _rows("workspace_chat_messages"),
            "workspace_glossary_terms": _rows("workspace_glossary_terms"),
            "workspace_expression_terms": _rows("workspace_expression_terms"),
            "worker_glossary_saves": _rows("worker_glossary_saves"),
        }


class SessionStore:
    """JWT (HS256) ベースのステートレスセッション。

    インメモリ dict は廃止。トークン自体に workspace_id / role / user_label /
    staff_account_id / exp が入っており、サーバ側に状態を持たない。

    効果:
      - 複数インスタンス（マルチプロセス・スケールアウト）でも全てのインスタンスが
        同じ秘密鍵で署名検証できる → ロードバランサ越しでも認証が通る。
      - 再起動時にセッションが消えない（トークン期限内なら再ログイン不要）。

    制約:
      - JWT は明示的に「revoke」できない（= 期限が来るまで有効）。必要なら
        revocation list を SQLite/PG/Redis に持つ拡張で対応する（将来の課題）。
    """

    @staticmethod
    def _algo() -> str:
        return "HS256"

    @staticmethod
    def _secret() -> str:
        from app.core.config import get_settings
        return get_settings().session_secret

    def create(
        self,
        workspace_id: str,
        role: Role,
        user_label: Optional[str],
        ttl_seconds: int,
        staff_account_id: Optional[str] = None,
        distributor_id: Optional[str] = None,
    ) -> Session:
        from jose import jwt

        now = time.time()
        exp = now + ttl_seconds
        claims = {
            "sub": workspace_id or "",
            "role": role.value,
            "lab": user_label or "",
            "sai": staff_account_id or "",
            "did": distributor_id or "",  # Phase 2.x: 소속 대리점 ID
            "iat": int(now),
            "exp": int(exp),
        }
        token = jwt.encode(claims, self._secret(), algorithm=self._algo())
        return Session(
            token=token,
            workspace_id=workspace_id,
            role=role,
            user_label=user_label,
            expires_at=exp,
            staff_account_id=staff_account_id,
            distributor_id=distributor_id or "",
        )

    def get(self, token: str) -> Optional[Session]:
        if not token:
            return None
        from jose import jwt
        from jose.exceptions import JWTError, ExpiredSignatureError
        try:
            claims = jwt.decode(token, self._secret(), algorithms=[self._algo()])
        except (JWTError, ExpiredSignatureError):
            return None
        try:
            role = Role(claims.get("role", ""))
        except ValueError:
            return None
        sub = claims.get("sub", "") or ""
        lab = claims.get("lab") or None
        sai = claims.get("sai") or None
        # Phase 2.x: did 클레임 (구 토큰엔 없어도 호환 — "" 로 처리)
        did = claims.get("did") or ""
        try:
            exp = float(claims.get("exp", 0))
        except (TypeError, ValueError):
            exp = 0.0
        return Session(
            token=token,
            workspace_id=sub,
            role=role,
            user_label=lab,
            expires_at=exp,
            staff_account_id=sai,
            distributor_id=did,
        )


# Singletons for MVP
workspaces = WorkspaceStore()
sessions = SessionStore()
