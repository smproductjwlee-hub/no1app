"""External webhook receivers — Phase 3.4.

현재는 Lemon Squeezy 만. 향후 다른 결제 게이트웨이가 생기면 같은 모듈에 추가.

설계 원칙:
- 인증: HMAC-SHA256 서명 검증 (X-Signature 헤더)
- 중복 처리: billing_events 의 idempotency_key 로 차단
- 안전 우선: 어떤 이벤트도 「알 수 없는 형식」 으로 인해 500 을 던지지 않도록
  except 로 감싸 200 OK 반환 (Lemon Squeezy 재전송 폭주 방지)
- DB 영속화: 매 이벤트마다 billing_events 에 raw payload 저장 (audit + 디버깅용)
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Request, status

from app.api.deps import run_db
from app.core.config import get_settings


router = APIRouter(prefix="/webhooks", tags=["webhooks"])


# ============================================================
# Helpers
# ============================================================


def _parse_iso8601_to_unix(s: Any) -> Optional[float]:
    """Lemon Squeezy 의 ISO8601 (e.g. '2026-06-15T10:30:00.000000Z') → UNIX 초.
    실패 시 None.
    """
    if not s or not isinstance(s, str):
        return None
    try:
        # python datetime 은 'Z' 를 직접 안 받음
        s2 = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s2)
        return dt.timestamp()
    except Exception:
        return None


def _extract_event_name(payload: dict) -> str:
    """Lemon Squeezy 의 event_name 추출.

    페이로드 구조:
        { "meta": { "event_name": "subscription_payment_success", ... }, "data": {...} }
    """
    meta = payload.get("meta") or {}
    return str(meta.get("event_name") or "")


def _extract_distributor_id(payload: dict) -> Optional[str]:
    """custom_data 또는 attributes 에서 distributor_id 추출.

    Phase 3.3 에서 checkout URL 만들 때 custom_data={'distributor_id': '...'} 로 넣음.
    Lemon Squeezy 는 이것을 attributes.first_subscription_item.subscription.custom_data
    또는 meta.custom_data 같은 경로로 echo 한다 (구버전·신버전 케이스 모두 지원).
    """
    # 가장 흔한 위치: meta.custom_data
    meta = payload.get("meta") or {}
    cd = meta.get("custom_data") or {}
    if isinstance(cd, dict) and cd.get("distributor_id"):
        return str(cd["distributor_id"])

    # 대안 위치: data.attributes.custom_data
    data = payload.get("data") or {}
    attrs = (data.get("attributes") if isinstance(data, dict) else None) or {}
    cd2 = attrs.get("custom_data") or {}
    if isinstance(cd2, dict) and cd2.get("distributor_id"):
        return str(cd2["distributor_id"])

    # checkout 페이로드의 경우 first_order_item 등에 들어있을 수도
    first_item = attrs.get("first_subscription_item") or attrs.get("first_order_item") or {}
    if isinstance(first_item, dict):
        cd3 = (first_item.get("custom_data") or {})
        if isinstance(cd3, dict) and cd3.get("distributor_id"):
            return str(cd3["distributor_id"])
    return None


def _extract_idempotency_key(payload: dict, headers: dict) -> Optional[str]:
    """idempotency_key 후보:

    1. X-Event-Id 헤더 (Lemon Squeezy 가 보낸다면)
    2. meta.event_id
    3. data.id + meta.event_name (안정적 조합)
    """
    eid = (
        headers.get("x-event-id")
        or headers.get("X-Event-Id")
        or (payload.get("meta") or {}).get("event_id")
    )
    if eid:
        return str(eid)
    data = payload.get("data") or {}
    if isinstance(data, dict) and data.get("id"):
        ev_name = (payload.get("meta") or {}).get("event_name", "evt")
        return f"{ev_name}:{data['id']}"
    return None


def _extract_subscription_id(payload: dict) -> Optional[str]:
    """data.id (data.type == 'subscriptions' 인 경우) 또는 subscription_id 필드."""
    data = payload.get("data") or {}
    if isinstance(data, dict):
        dtype = str(data.get("type") or "")
        did = data.get("id")
        if dtype == "subscriptions" and did:
            return str(did)
        # 결제 이벤트는 data.type == 'subscription-invoices'. subscription_id 는 attributes 에.
        attrs = data.get("attributes") or {}
        sid = attrs.get("subscription_id")
        if sid:
            return str(sid)
    return None


def _extract_amount_cents_and_currency(payload: dict) -> tuple[Optional[int], Optional[str]]:
    """결제 이벤트의 금액 (총액, 통화). 없으면 (None, None)."""
    data = payload.get("data") or {}
    attrs = (data.get("attributes") if isinstance(data, dict) else None) or {}
    # Subscription invoice 류는 attributes.total / .currency
    amount = attrs.get("total") if "total" in attrs else attrs.get("subtotal")
    currency = attrs.get("currency")
    try:
        amount_int = int(amount) if amount is not None else None
    except (TypeError, ValueError):
        amount_int = None
    return amount_int, (str(currency).upper() if currency else None)


# ============================================================
# Main webhook endpoint
# ============================================================


# 자동 suspended 임계값 (연속 미납 N 회)
_AUTO_SUSPEND_FAILURE_THRESHOLD = 3


@router.post("/lemon-squeezy", status_code=status.HTTP_200_OK)
async def lemon_squeezy_webhook(
    request: Request,
    x_signature: Optional[str] = Header(default=None, alias="X-Signature"),
) -> dict:
    """Lemon Squeezy webhook 수신점.

    응답은 항상 200 (서명 실패 시만 401). 알 수 없는 이벤트 / 매핑 실패는
    payload 만 저장하고 200 반환 — Lemon Squeezy 재전송 폭주 방지.
    """
    from app.services.billing import events as billing_events
    from app.services.billing.lemon_squeezy import verify_webhook_signature
    from app.services.distributors import distributors as distributors_store

    settings = get_settings()
    raw = await request.body()

    # ----- 1. 서명 검증 -----
    if settings.lemon_webhook_secret:
        ok = verify_webhook_signature(
            raw_body=raw,
            signature_header=(x_signature or "").strip(),
        )
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid signature",
            )
    # secret 미설정 시 (개발 환경) 서명 스킵 — production 에서는 반드시 설정

    # ----- 2. payload 파싱 -----
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid JSON body",
        )
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="payload must be a JSON object",
        )

    event_name = _extract_event_name(payload)
    idem_key = _extract_idempotency_key(payload, dict(request.headers))
    distributor_id = _extract_distributor_id(payload)
    sub_id = _extract_subscription_id(payload)
    amount_cents, currency = _extract_amount_cents_and_currency(payload)

    # ----- 3. 중복 webhook 차단 -----
    if idem_key:
        already = await run_db(billing_events.is_processed, idem_key)
        if already:
            return {"ok": True, "duplicate": True, "event": event_name}

    # ----- 4. distributor 매핑: custom_data 없으면 sub_id 로 검색 -----
    if not distributor_id and sub_id:
        d_match = await run_db(distributors_store.get_by_lemon_subscription, sub_id)
        if d_match is not None:
            distributor_id = d_match.id

    # ----- 5. 이벤트별 처리 -----
    try:
        await _dispatch_event(
            event_name=event_name,
            payload=payload,
            distributor_id=distributor_id,
            sub_id=sub_id,
            amount_cents=amount_cents,
            currency=currency,
        )
    except Exception:
        # 핸들러 안에서 예외가 나도 webhook 자체는 200 OK 로 응답 (재전송 폭주 방지).
        # 단, billing_events 에는 저장하지 않음 (재시도 가능하도록) → 아니다, 항상 저장.
        import traceback as _tb
        _tb.print_exc()

    # ----- 6. billing_events 영속화 (audit) -----
    try:
        await run_db(
            billing_events.record,
            event_type=event_name or "unknown",
            payload=payload,
            distributor_id=distributor_id,
            idempotency_key=idem_key,
            amount_cents=amount_cents,
            currency=currency,
        )
    except Exception:
        # idempotency 충돌 외의 진짜 에러도 200 으로. Lemon 의 retry 폭주 방지.
        import traceback as _tb
        _tb.print_exc()

    return {
        "ok": True,
        "event": event_name,
        "distributor_id": distributor_id,
        "subscription_id": sub_id,
    }


# ============================================================
# Per-event dispatch
# ============================================================


async def _dispatch_event(
    *,
    event_name: str,
    payload: dict,
    distributor_id: Optional[str],
    sub_id: Optional[str],
    amount_cents: Optional[int],
    currency: Optional[str],
) -> None:
    """Lemon Squeezy 의 각 event_name 에 대해 distributor 의 lifecycle 메소드를 호출."""
    from app.services.distributors import distributors as distributors_store

    # 모든 event 가 distributor 와 연결되어 있어야 의미가 있음.
    if not distributor_id:
        return  # 매핑 실패 — billing_events 에만 audit 저장하고 종료

    data = payload.get("data") or {}
    attrs = (data.get("attributes") if isinstance(data, dict) else None) or {}

    if event_name == "subscription_created":
        # 첫 구독 시작. customer_id, subscription_id 매핑.
        customer_id = attrs.get("customer_id")
        renews_at = _parse_iso8601_to_unix(attrs.get("renews_at"))
        await run_db(
            distributors_store.attach_subscription,
            distributor_id,
            lemon_customer_id=str(customer_id) if customer_id else None,
            lemon_subscription_id=sub_id,
            subscription_status="active",
            subscription_renews_at=renews_at,
        )
        return

    if event_name == "subscription_updated":
        # 구독 일반 변경 (status / renews_at).
        new_status = str(attrs.get("status") or "").lower()
        renews_at = _parse_iso8601_to_unix(attrs.get("renews_at"))
        # Lemon Squeezy 의 status 를 우리 도메인 enum 으로 매핑
        # 「on_trial / active / paused / past_due / cancelled / expired」 그대로 사용
        # 그 외는 None → status 미변경
        if new_status in ("none", "pending", "on_trial", "active", "past_due", "paused", "cancelled", "expired"):
            await run_db(
                distributors_store.set_subscription_status,
                distributor_id,
                new_status,
                renews_at=renews_at,
            )
        return

    if event_name == "subscription_payment_success":
        next_renews = _parse_iso8601_to_unix(attrs.get("renews_at") or attrs.get("created_at"))
        await run_db(
            distributors_store.record_payment_success,
            distributor_id,
            amount_cents=int(amount_cents or 0),
            paid_at=_parse_iso8601_to_unix(attrs.get("created_at")),
            next_renews_at=next_renews,
        )
        return

    if event_name == "subscription_payment_failed":
        updated = await run_db(
            distributors_store.record_payment_failure,
            distributor_id,
        )
        # N 회 연속 실패 → 운영 status 도 suspended 로 자동 전이
        if updated and updated.payment_failure_count >= _AUTO_SUSPEND_FAILURE_THRESHOLD:
            try:
                await run_db(
                    distributors_store.set_status,
                    distributor_id,
                    "suspended",
                )
            except Exception:
                pass
        return

    if event_name == "subscription_payment_recovered":
        # 미납 후 복구 — status 를 active 로
        await run_db(
            distributors_store.record_payment_success,
            distributor_id,
            amount_cents=int(amount_cents or 0),
            paid_at=_parse_iso8601_to_unix(attrs.get("created_at")),
        )
        # 운영자가 사전에 suspended 해뒀더라도 복구는 운영자 판단으로 (자동 active 복귀 안 함)
        return

    if event_name == "subscription_cancelled":
        await run_db(
            distributors_store.set_subscription_status,
            distributor_id,
            "cancelled",
        )
        return

    if event_name == "subscription_expired":
        await run_db(
            distributors_store.set_subscription_status,
            distributor_id,
            "expired",
        )
        return

    if event_name == "subscription_paused":
        await run_db(
            distributors_store.set_subscription_status,
            distributor_id,
            "paused",
        )
        return

    if event_name == "subscription_resumed":
        await run_db(
            distributors_store.set_subscription_status,
            distributor_id,
            "active",
        )
        return

    # 그 외 이벤트 (order_created 등) 는 billing_events 에 audit 만 저장
    return
