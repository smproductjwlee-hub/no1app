"""販売代理店 (Distributor) ドメインサービス — Phase 2.2.

3계층 멀티테넌시 (Super Admin → Distributor → Workspace) の中間層を管理。
도매가는 distributors 테이블에 보관되며 운영자(super admin)가 직접 조정한다.

- 大리점 등록·로그인·도매가 수정 (운영자 권한)
- 대리점이 자기 산하 워크스페이스를 보거나 추가하는 헬퍼 (대리점 admin 권한)
- c-direct (직판) 가상 대리점은 init_db 에서 시드됨

비밀번호 해싱: staff_accounts.py 와 동일한 pbkdf2_sha256.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from passlib.context import CryptContext

from app.db.sqlite import get_connection, is_unique_violation, make_slug_from_name


# bcrypt と passlib の組み合わせで環境により失敗するため、標準互換の PBKDF2 を使用
_pwd = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


# ============================================================
# Dataclass
# ============================================================


@dataclass
class Distributor:
    id: str
    slug: str
    name: str
    contact_person: str = ""
    contact_phone: str = ""
    contact_email: str = ""
    owner_email: str = ""
    owner_password_hash: str = ""
    wholesale_starter: int = 8000
    wholesale_business: int = 6500
    wholesale_enterprise: int = 5000
    wholesale_mvp_fee: int = 5000000
    force_password_change_on_login: bool = False
    status: str = "active"  # active / suspended
    created_at: float = 0.0
    updated_at: float = 0.0
    # Phase 3.2 — Lemon Squeezy subscription lifecycle
    lemon_customer_id: Optional[str] = None
    lemon_subscription_id: Optional[str] = None
    subscription_status: str = "none"  # none / pending / on_trial / active / past_due / paused / cancelled / expired
    subscription_renews_at: Optional[float] = None  # 다음 결제 예정일 (UNIX sec)
    payment_failure_count: int = 0
    last_payment_at: Optional[float] = None
    last_payment_amount_cents: Optional[int] = None

    def is_active(self) -> bool:
        return self.status == "active"

    def is_paying(self) -> bool:
        """현재 정상 결제 흐름 안에 있는지 (운영 청구 책임 대상)."""
        return self.subscription_status in ("active", "on_trial")


# ============================================================
# Row → Dataclass
# ============================================================


def _row(row) -> Distributor:
    keys = row.keys() if hasattr(row, "keys") else None

    def _g(name: str, default: Any = ""):
        if keys is None:
            return default
        if name not in keys:
            return default
        v = row[name]
        if v is None:
            return default
        return v

    def _gi(name: str, default: int) -> int:
        v = _g(name, default)
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    def _gs(name: str) -> Optional[str]:
        """NULL/빈 값 → None, 그 외 → str."""
        if keys is None or name not in keys:
            return None
        v = row[name]
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    def _gf(name: str) -> Optional[float]:
        if keys is None or name not in keys:
            return None
        v = row[name]
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _gi_opt(name: str) -> Optional[int]:
        if keys is None or name not in keys:
            return None
        v = row[name]
        if v is None:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    return Distributor(
        id=str(_g("id", "")),
        slug=str(_g("slug", "")).strip(),
        name=str(_g("name", "")),
        contact_person=str(_g("contact_person", "")),
        contact_phone=str(_g("contact_phone", "")),
        contact_email=str(_g("contact_email", "")),
        owner_email=str(_g("owner_email", "")),
        owner_password_hash=str(_g("owner_password_hash", "")),
        wholesale_starter=_gi("wholesale_starter", 8000),
        wholesale_business=_gi("wholesale_business", 6500),
        wholesale_enterprise=_gi("wholesale_enterprise", 5000),
        wholesale_mvp_fee=_gi("wholesale_mvp_fee", 5000000),
        force_password_change_on_login=bool(_gi("force_password_change_on_login", 0)),
        status=str(_g("status", "active")) or "active",
        created_at=float(_g("created_at", 0.0) or 0.0),
        updated_at=float(_g("updated_at", 0.0) or 0.0),
        lemon_customer_id=_gs("lemon_customer_id"),
        lemon_subscription_id=_gs("lemon_subscription_id"),
        subscription_status=str(_g("subscription_status", "none")) or "none",
        subscription_renews_at=_gf("subscription_renews_at"),
        payment_failure_count=_gi("payment_failure_count", 0),
        last_payment_at=_gf("last_payment_at"),
        last_payment_amount_cents=_gi_opt("last_payment_amount_cents"),
    )


# ============================================================
# Password helpers
# ============================================================


def hash_password(raw: str) -> str:
    """평문 비밀번호 → pbkdf2_sha256 해시."""
    return _pwd.hash(raw or "")


def verify_password(raw: str, hashed: str) -> bool:
    """평문 ↔ 해시 검증. hashed 가 빈 문자열이면 항상 False."""
    if not hashed:
        return False
    try:
        return _pwd.verify(raw or "", hashed)
    except Exception:
        return False


# ============================================================
# DistributorStore
# ============================================================


class DistributorStore:
    def create(
        self,
        slug: str,
        name: str,
        *,
        owner_email: str = "",
        owner_password: str = "",
        contact_person: str = "",
        contact_phone: str = "",
        contact_email: str = "",
        wholesale_starter: int = 8000,
        wholesale_business: int = 6500,
        wholesale_enterprise: int = 5000,
        wholesale_mvp_fee: int = 5000000,
        force_password_change_on_login: bool = False,
    ) -> Distributor:
        """대리점 신규 등록. slug 는 UNIQUE 이므로 충돌 시 ValueError."""
        slug = (slug or "").strip().lower()
        if not slug:
            raise ValueError("slug is required")
        # 정규화: 영문 소문자·숫자·하이픈만, 3-20자
        normalized = make_slug_from_name(slug, fallback_id="")
        if normalized == "ws-tmp" or len(normalized) < 3:
            raise ValueError(f"invalid slug '{slug}'; must be lowercase alphanumeric + hyphen, 3-20 chars")
        # ws- prefix 는 워크스페이스 fallback 용이므로 distributor 슬러그에는 비허용
        if normalized.startswith("ws-"):
            raise ValueError("distributor slug cannot start with 'ws-'")
        slug = normalized

        name = (name or "").strip()
        if not name:
            raise ValueError("name is required")

        password_hash = hash_password(owner_password) if owner_password else ""

        d_id = str(uuid.uuid4())
        now = time.time()
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO distributors (
                    id, slug, name, contact_person, contact_phone, contact_email,
                    owner_email, owner_password_hash,
                    wholesale_starter, wholesale_business, wholesale_enterprise, wholesale_mvp_fee,
                    force_password_change_on_login, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    d_id, slug, name,
                    (contact_person or "").strip(),
                    (contact_phone or "").strip(),
                    (contact_email or "").strip(),
                    (owner_email or "").strip(),
                    password_hash,
                    max(0, int(wholesale_starter)),
                    max(0, int(wholesale_business)),
                    max(0, int(wholesale_enterprise)),
                    max(0, int(wholesale_mvp_fee)),
                    1 if force_password_change_on_login else 0,
                    "active",
                    now, now,
                ),
            )
            conn.commit()
        except Exception as exc:
            if is_unique_violation(exc):
                raise ValueError(f"distributor slug '{slug}' already exists") from exc
            raise
        r = conn.execute("SELECT * FROM distributors WHERE id = ?", (d_id,)).fetchone()
        assert r is not None
        return _row(r)

    def get(self, distributor_id: str) -> Optional[Distributor]:
        if not distributor_id:
            return None
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM distributors WHERE id = ?", (distributor_id,)
        ).fetchone()
        return _row(row) if row else None

    def get_by_slug(self, slug: str) -> Optional[Distributor]:
        if not slug:
            return None
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM distributors WHERE slug = ?",
            ((slug or "").strip().lower(),),
        ).fetchone()
        return _row(row) if row else None

    def get_by_owner_email(self, email: str) -> Optional[Distributor]:
        if not email:
            return None
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM distributors WHERE lower(trim(owner_email)) = ? LIMIT 1",
            ((email or "").strip().lower(),),
        ).fetchone()
        return _row(row) if row else None

    def list_all(self) -> list[Distributor]:
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM distributors ORDER BY status DESC, created_at ASC"
        ).fetchall()
        return [_row(r) for r in rows]

    def update_contact(
        self,
        distributor_id: str,
        *,
        contact_person: Optional[str] = None,
        contact_phone: Optional[str] = None,
        contact_email: Optional[str] = None,
        name: Optional[str] = None,
    ) -> Optional[Distributor]:
        """좌상단 표시용 연락처 정보 갱신 (대리점·운영자 모두 가능)."""
        sets: list[str] = []
        vals: list[Any] = []
        if contact_person is not None:
            sets.append("contact_person = ?")
            vals.append(contact_person.strip())
        if contact_phone is not None:
            sets.append("contact_phone = ?")
            vals.append(contact_phone.strip())
        if contact_email is not None:
            sets.append("contact_email = ?")
            vals.append(contact_email.strip())
        if name is not None:
            sets.append("name = ?")
            vals.append(name.strip())
        if not sets:
            return self.get(distributor_id)
        sets.append("updated_at = ?")
        vals.append(time.time())
        vals.append(distributor_id)
        conn = get_connection()
        conn.execute(
            "UPDATE distributors SET " + ", ".join(sets) + " WHERE id = ?", vals
        )
        conn.commit()
        return self.get(distributor_id)

    def update_wholesale(
        self,
        distributor_id: str,
        *,
        wholesale_starter: Optional[int] = None,
        wholesale_business: Optional[int] = None,
        wholesale_enterprise: Optional[int] = None,
        wholesale_mvp_fee: Optional[int] = None,
    ) -> Optional[Distributor]:
        """도매가 갱신 (운영자 권한만). 모든 인자 None 이면 변경 없음."""
        sets: list[str] = []
        vals: list[Any] = []
        for col, v in (
            ("wholesale_starter", wholesale_starter),
            ("wholesale_business", wholesale_business),
            ("wholesale_enterprise", wholesale_enterprise),
            ("wholesale_mvp_fee", wholesale_mvp_fee),
        ):
            if v is not None:
                try:
                    n = max(0, int(v))
                except (TypeError, ValueError):
                    continue
                sets.append(f"{col} = ?")
                vals.append(n)
        if not sets:
            return self.get(distributor_id)
        sets.append("updated_at = ?")
        vals.append(time.time())
        vals.append(distributor_id)
        conn = get_connection()
        conn.execute(
            "UPDATE distributors SET " + ", ".join(sets) + " WHERE id = ?", vals
        )
        conn.commit()
        return self.get(distributor_id)

    def update_owner_login(
        self,
        distributor_id: str,
        *,
        owner_email: Optional[str] = None,
        new_password: Optional[str] = None,
        force_password_change_on_login: Optional[bool] = None,
    ) -> Optional[Distributor]:
        """로그인 정보 갱신. new_password 가 있으면 해싱해서 저장."""
        sets: list[str] = []
        vals: list[Any] = []
        if owner_email is not None:
            sets.append("owner_email = ?")
            vals.append(owner_email.strip())
        if new_password is not None and new_password != "":
            sets.append("owner_password_hash = ?")
            vals.append(hash_password(new_password))
        if force_password_change_on_login is not None:
            sets.append("force_password_change_on_login = ?")
            vals.append(1 if force_password_change_on_login else 0)
        if not sets:
            return self.get(distributor_id)
        sets.append("updated_at = ?")
        vals.append(time.time())
        vals.append(distributor_id)
        conn = get_connection()
        conn.execute(
            "UPDATE distributors SET " + ", ".join(sets) + " WHERE id = ?", vals
        )
        conn.commit()
        return self.get(distributor_id)

    def set_status(self, distributor_id: str, status: str) -> Optional[Distributor]:
        """active / suspended 토글."""
        s = (status or "").strip().lower()
        if s not in ("active", "suspended"):
            raise ValueError(f"invalid status '{status}'; must be 'active' or 'suspended'")
        conn = get_connection()
        conn.execute(
            "UPDATE distributors SET status = ?, updated_at = ? WHERE id = ?",
            (s, time.time(), distributor_id),
        )
        conn.commit()
        return self.get(distributor_id)

    def authenticate(self, owner_email: str, password: str) -> Optional[Distributor]:
        """로그인 검증. status=suspended 면 None 반환 (로그인 차단)."""
        d = self.get_by_owner_email(owner_email)
        if d is None:
            return None
        if not d.is_active():
            return None
        if not verify_password(password, d.owner_password_hash):
            return None
        return d

    def delete_with_cascade(self, distributor_id: str) -> dict:
        """대리점 + 그 산하 모든 워크스페이스 및 데이터 일괄 삭제.

        c-direct (직판) 는 삭제 금지 (시스템 무결성).
        """
        d = self.get(distributor_id)
        if d is None:
            return {"distributors": 0, "workspaces": 0, "files_deleted": 0}
        if d.slug == "c-direct":
            raise ValueError("cannot delete the system 'c-direct' distributor")

        from app.services.stores import workspaces as _ws_store

        conn = get_connection()
        ws_rows = conn.execute(
            "SELECT id FROM workspaces WHERE distributor_id = ?", (distributor_id,)
        ).fetchall()
        ws_ids = [r[0] if not hasattr(r, "keys") else r["id"] for r in ws_rows]

        counts = {"workspaces": 0, "files_deleted": 0}
        for ws_id in ws_ids:
            try:
                sub = _ws_store.delete_with_cascade(ws_id)
                counts["workspaces"] += int(sub.get("workspaces", 0) or 0)
                counts["files_deleted"] += int(sub.get("files_deleted", 0) or 0)
            except Exception:
                # 1개 실패해도 다른 워크스페이스 삭제는 계속
                pass

        cur = conn.execute("DELETE FROM distributors WHERE id = ?", (distributor_id,))
        counts["distributors"] = int(getattr(cur, "rowcount", 0) or 0)
        conn.commit()
        return counts

    def get_c_direct(self) -> Optional[Distributor]:
        """직판 가상 대리점 조회. init_db 후에는 항상 존재."""
        return self.get_by_slug("c-direct")

    # ============================================================
    # Phase 3.2 — Lemon Squeezy subscription lifecycle
    # ============================================================

    def get_by_lemon_subscription(self, lemon_subscription_id: str) -> Optional[Distributor]:
        """Webhook 핸들러용: Lemon Squeezy subscription_id 로 대리점 찾기.

        Lemon Squeezy 가 보내는 모든 이벤트는 subscription_id 를 포함하므로 이 헬퍼로
        webhook payload → distributor 매핑이 가능하다.
        """
        if not lemon_subscription_id:
            return None
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM distributors WHERE lemon_subscription_id = ? LIMIT 1",
            (str(lemon_subscription_id),),
        ).fetchone()
        return _row(row) if row else None

    def attach_subscription(
        self,
        distributor_id: str,
        *,
        lemon_customer_id: Optional[str] = None,
        lemon_subscription_id: Optional[str] = None,
        subscription_status: Optional[str] = None,
        subscription_renews_at: Optional[float] = None,
    ) -> Optional[Distributor]:
        """Lemon Squeezy 구독을 대리점에 연결 (subscription_created webhook 또는 첫 결제 직후).

        None 인 인자는 변경 안 함. 명시적으로 클리어하려면 빈 문자열/0 을 명시.
        """
        sets: list[str] = []
        vals: list[Any] = []
        if lemon_customer_id is not None:
            sets.append("lemon_customer_id = ?")
            vals.append(str(lemon_customer_id).strip() or None)
        if lemon_subscription_id is not None:
            sets.append("lemon_subscription_id = ?")
            vals.append(str(lemon_subscription_id).strip() or None)
        if subscription_status is not None:
            sets.append("subscription_status = ?")
            vals.append(str(subscription_status).strip() or "none")
        if subscription_renews_at is not None:
            try:
                sets.append("subscription_renews_at = ?")
                vals.append(float(subscription_renews_at))
            except (TypeError, ValueError):
                pass
        if not sets:
            return self.get(distributor_id)
        sets.append("updated_at = ?")
        vals.append(time.time())
        vals.append(distributor_id)
        conn = get_connection()
        conn.execute(
            f"UPDATE distributors SET {', '.join(sets)} WHERE id = ?",
            vals,
        )
        conn.commit()
        return self.get(distributor_id)

    def record_payment_success(
        self,
        distributor_id: str,
        *,
        amount_cents: int,
        paid_at: Optional[float] = None,
        next_renews_at: Optional[float] = None,
    ) -> Optional[Distributor]:
        """결제 성공 webhook 처리. 미납 카운터 0 리셋."""
        ts = paid_at if paid_at is not None else time.time()
        sets = [
            "last_payment_at = ?",
            "last_payment_amount_cents = ?",
            "payment_failure_count = 0",
            "subscription_status = 'active'",
            "updated_at = ?",
        ]
        vals: list[Any] = [float(ts), int(amount_cents or 0), time.time()]
        if next_renews_at is not None:
            try:
                sets.append("subscription_renews_at = ?")
                vals.append(float(next_renews_at))
            except (TypeError, ValueError):
                pass
        vals.append(distributor_id)
        conn = get_connection()
        conn.execute(
            f"UPDATE distributors SET {', '.join(sets)} WHERE id = ?",
            vals,
        )
        conn.commit()
        return self.get(distributor_id)

    def record_payment_failure(self, distributor_id: str) -> Optional[Distributor]:
        """결제 실패 webhook 처리. 미납 카운터 +1, subscription_status='past_due'.

        호출자가 카운터를 보고 「N 회 이상이면 status=suspended」 같은 정책을 결정한다.
        """
        conn = get_connection()
        conn.execute(
            """
            UPDATE distributors
            SET payment_failure_count = payment_failure_count + 1,
                subscription_status = 'past_due',
                updated_at = ?
            WHERE id = ?
            """,
            (time.time(), distributor_id),
        )
        conn.commit()
        return self.get(distributor_id)

    def set_subscription_status(
        self,
        distributor_id: str,
        status: str,
        *,
        renews_at: Optional[float] = None,
    ) -> Optional[Distributor]:
        """subscription_status 만 갱신 (subscription_cancelled / _resumed 등에 사용)."""
        valid = {"none", "pending", "on_trial", "active", "past_due", "paused", "cancelled", "expired"}
        if status not in valid:
            raise ValueError(f"invalid subscription_status: {status!r}")
        sets = ["subscription_status = ?", "updated_at = ?"]
        vals: list[Any] = [status, time.time()]
        if renews_at is not None:
            try:
                sets.append("subscription_renews_at = ?")
                vals.append(float(renews_at))
            except (TypeError, ValueError):
                pass
        vals.append(distributor_id)
        conn = get_connection()
        conn.execute(
            f"UPDATE distributors SET {', '.join(sets)} WHERE id = ?",
            vals,
        )
        conn.commit()
        return self.get(distributor_id)


# Singleton
distributors = DistributorStore()
