"""billing_events 테이블 액세스 — Phase 3.2.

Lemon Squeezy webhook 의 모든 이벤트를 audit·idempotency 목적으로 저장한다.

- record(): 이벤트 한 건 저장. idempotency_key (event_id) 가 이미 있으면 no-op.
- is_processed(): webhook 핸들러가 처음 호출 시 빠르게 중복 확인.
- list_for_distributor(): super admin / distributor admin 의 「결제 이력」 UI 용.

idempotency_key 는 Lemon Squeezy 의 webhook payload 에 들어가는
event_id (또는 attributes.event_name + meta.uuid 등 호출자가 만든 안정적 key).
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from app.db.sqlite import get_connection, is_unique_violation


@dataclass
class BillingEvent:
    id: str
    distributor_id: Optional[str]
    event_type: str
    idempotency_key: Optional[str]
    payload_json: str
    amount_cents: Optional[int]
    currency: Optional[str]
    created_at: float

    def payload(self) -> Any:
        """payload_json 을 dict 로 파싱 (실패시 빈 dict)."""
        try:
            return json.loads(self.payload_json) if self.payload_json else {}
        except Exception:
            return {}


def _row_to_event(row) -> BillingEvent:
    keys = row.keys() if hasattr(row, "keys") else None

    def _g(name: str, default: Any = None) -> Any:
        if keys is None or name not in keys:
            return default
        v = row[name]
        return default if v is None else v

    return BillingEvent(
        id=str(_g("id", "")),
        distributor_id=(str(_g("distributor_id", "")) or None),
        event_type=str(_g("event_type", "")),
        idempotency_key=(str(_g("idempotency_key", "")) or None),
        payload_json=str(_g("payload_json", "") or ""),
        amount_cents=(int(_g("amount_cents")) if _g("amount_cents") is not None else None),
        currency=(str(_g("currency", "")) or None),
        created_at=float(_g("created_at", 0.0) or 0.0),
    )


def is_processed(idempotency_key: str) -> bool:
    """이 idempotency_key 의 이벤트가 이미 저장되어 있으면 True (= webhook 중복)."""
    if not idempotency_key:
        return False
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM billing_events WHERE idempotency_key = ? LIMIT 1",
        (str(idempotency_key),),
    ).fetchone()
    return row is not None


def record(
    *,
    event_type: str,
    payload: Any,
    distributor_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    amount_cents: Optional[int] = None,
    currency: Optional[str] = None,
) -> Optional[BillingEvent]:
    """이벤트 1 건 영속화. idempotency_key 중복이면 None 반환 (no-op).

    payload 는 dict / list / str 등 모두 JSON 직렬화 가능한 값.
    """
    event_type = (event_type or "").strip()
    if not event_type:
        raise ValueError("event_type is required")
    try:
        payload_str = json.dumps(payload, ensure_ascii=False) if payload is not None else ""
    except Exception:
        payload_str = str(payload)
    new_id = str(uuid.uuid4())
    ts = time.time()
    cur_str = str(currency).strip().upper() if currency else None
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO billing_events
                (id, distributor_id, event_type, idempotency_key, payload_json,
                 amount_cents, currency, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id,
                str(distributor_id) if distributor_id else None,
                event_type,
                str(idempotency_key) if idempotency_key else None,
                payload_str,
                int(amount_cents) if amount_cents is not None else None,
                cur_str,
                ts,
            ),
        )
        conn.commit()
    except Exception as exc:
        if is_unique_violation(exc):
            # 같은 idempotency_key 가 이미 있음 → 중복 webhook. 정상.
            return None
        raise
    return BillingEvent(
        id=new_id,
        distributor_id=str(distributor_id) if distributor_id else None,
        event_type=event_type,
        idempotency_key=str(idempotency_key) if idempotency_key else None,
        payload_json=payload_str,
        amount_cents=int(amount_cents) if amount_cents is not None else None,
        currency=cur_str,
        created_at=ts,
    )


def list_for_distributor(distributor_id: str, *, limit: int = 50) -> list[BillingEvent]:
    """대리점의 최근 결제 이벤트 (UI 의 「お支払い履歴」 패널용)."""
    if not distributor_id:
        return []
    limit = max(1, min(int(limit or 50), 500))
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT * FROM billing_events
        WHERE distributor_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (str(distributor_id), limit),
    ).fetchall()
    return [_row_to_event(r) for r in rows]


def list_recent(*, limit: int = 100) -> list[BillingEvent]:
    """전체 이벤트 최근순 (super admin 의 「決済ログ」 화면용)."""
    limit = max(1, min(int(limit or 100), 500))
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT * FROM billing_events
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_row_to_event(r) for r in rows]
