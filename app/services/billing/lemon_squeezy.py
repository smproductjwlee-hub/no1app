"""Lemon Squeezy API 클라이언트 (Subscription 모델).

설계 결정:
- 대리점 = Lemon Squeezy 의 「subscriber」
- 매월 자동 청구: 「산하 워크스페이스 수」 × 「플랜별 도매가」
- MVP 開発費 (¥5M one-time) 은 별도 checkout (subscription 과 분리)
- Webhook 으로 결제 성공·실패·취소 이벤트 수신

본 모듈은 외부 의존성으로 `httpx` 만 사용 (requirements.txt 에 명시).

환경 변수 (모두 빈 값이면 결제 기능 비활성화 — 운영 영향 없음):
- LEMON_API_KEY              필수
- LEMON_STORE_ID             필수
- LEMON_VARIANT_STARTER      Phase 3.3 부터 사용
- LEMON_VARIANT_BUSINESS
- LEMON_VARIANT_ENTERPRISE
- LEMON_VARIANT_MVP_FEE
- LEMON_WEBHOOK_SECRET       Phase 3.4 부터 사용
- LEMON_TEST_MODE            기본 True (안전한 default)
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any, Optional

import httpx

from app.core.config import get_settings


# -----------------------------------------------------------------------------
# Configuration helper
# -----------------------------------------------------------------------------


def is_configured() -> bool:
    """API key 와 store ID 가 모두 설정되어 있을 때 True."""
    s = get_settings()
    return bool(s.lemon_api_key and s.lemon_store_id)


def is_fully_configured() -> bool:
    """API + store + 4 variant + webhook secret 까지 모두 설정되어 있을 때 True.

    Phase 3.3+ 의 실제 구독 흐름은 모든 variant 가 필요하다.
    """
    s = get_settings()
    return bool(
        s.lemon_api_key
        and s.lemon_store_id
        and s.lemon_variant_starter
        and s.lemon_variant_business
        and s.lemon_variant_enterprise
        and s.lemon_variant_mvp_fee
        and s.lemon_webhook_secret
    )


# -----------------------------------------------------------------------------
# API Client
# -----------------------------------------------------------------------------


class LemonSqueezyError(Exception):
    """Lemon Squeezy API 호출이 실패했을 때 모든 호출자가 잡을 수 있는 단일 예외."""

    def __init__(self, message: str, *, status_code: Optional[int] = None, response: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class LemonSqueezyClient:
    """Lemon Squeezy REST API 클라이언트.

    스레드 안전: httpx.Client 가 매 호출마다 새 컨텍스트로 열림 (sync). 운영 트래픽이
    매우 높아지면 인스턴스 풀링으로 바꿀 수 있지만 결제 호출은 빈도가 낮아 현재로는 단순함이 우선.
    """

    BASE_URL = "https://api.lemonsqueezy.com/v1"
    DEFAULT_TIMEOUT = 15.0  # seconds

    def __init__(
        self,
        api_key: Optional[str] = None,
        store_id: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        s = get_settings()
        self.api_key = api_key or s.lemon_api_key
        self.store_id = store_id or s.lemon_store_id
        self.timeout = timeout
        if not self.api_key:
            raise LemonSqueezyError("LEMON_API_KEY is not configured")

    # -------- Low-level HTTP --------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/vnd.api+json",
            "Content-Type": "application/vnd.api+json",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        url = f"{self.BASE_URL}{path}"
        try:
            with httpx.Client(timeout=self.timeout) as c:
                r = c.request(method, url, headers=self._headers(), params=params, json=json)
        except httpx.HTTPError as e:
            raise LemonSqueezyError(f"network error: {e}") from e
        if r.status_code >= 400:
            body_preview: Any
            try:
                body_preview = r.json()
            except Exception:
                body_preview = r.text[:500]
            raise LemonSqueezyError(
                f"HTTP {r.status_code} on {method} {path}: {body_preview}",
                status_code=r.status_code,
                response=body_preview,
            )
        if not r.content:
            return {}
        try:
            return r.json()
        except ValueError as e:
            raise LemonSqueezyError(f"invalid JSON in response: {e}") from e

    # -------- High-level helpers --------

    def whoami(self) -> dict[str, Any]:
        """API 키 유효성 확인용. /users/me 응답 반환."""
        return self._request("GET", "/users/me")

    def get_store(self) -> dict[str, Any]:
        """현재 store 정보 (이름·통화 등)."""
        if not self.store_id:
            raise LemonSqueezyError("store_id not configured")
        return self._request("GET", f"/stores/{self.store_id}")

    def list_products(self, page_size: int = 50) -> list[dict[str, Any]]:
        """store 내의 상품 일람. variant ID 확인용."""
        params = {"page[size]": page_size}
        if self.store_id:
            params["filter[store_id]"] = self.store_id
        data = self._request("GET", "/products", params=params)
        return list(data.get("data") or [])

    def list_variants(self, product_id: Optional[str] = None, page_size: int = 100) -> list[dict[str, Any]]:
        """상품의 variant 일람. variant ID 가 .env 설정과 맞는지 확인할 때 사용."""
        params: dict[str, Any] = {"page[size]": page_size}
        if product_id:
            params["filter[product_id]"] = product_id
        data = self._request("GET", "/variants", params=params)
        return list(data.get("data") or [])

    def get_subscription(self, subscription_id: str) -> dict[str, Any]:
        return self._request("GET", f"/subscriptions/{subscription_id}")

    def update_subscription_quantity(self, subscription_id: str, quantity: int) -> dict[str, Any]:
        """산하 워크스페이스 수가 바뀌면 호출 (Phase 3.3+).

        Lemon Squeezy 는 PATCH 로 quantity 변경 → proration 자동 처리.
        """
        body = {
            "data": {
                "type": "subscriptions",
                "id": str(subscription_id),
                "attributes": {"quantity": int(quantity)},
            }
        }
        return self._request("PATCH", f"/subscriptions/{subscription_id}", json=body)

    def cancel_subscription(self, subscription_id: str) -> dict[str, Any]:
        """구독 즉시 취소. (Renewal 이 발생하지 않음.)"""
        return self._request("DELETE", f"/subscriptions/{subscription_id}")

    def create_subscription_checkout_url(
        self,
        *,
        variant_id: str,
        customer_email: str,
        customer_name: str = "",
        custom_data: Optional[dict[str, Any]] = None,
        quantity: int = 1,
        redirect_url: Optional[str] = None,
    ) -> str:
        """대리점이 구독 가입할 수 있는 Checkout URL 생성.

        custom_data 에 distributor_id 를 넣으면 webhook 에서 받아 DB 와 매핑할 수 있다.
        """
        if not self.store_id:
            raise LemonSqueezyError("store_id not configured")
        attributes: dict[str, Any] = {
            "checkout_data": {
                "email": customer_email,
                "custom": custom_data or {},
            },
            "checkout_options": {
                "embed": False,
                "dark": False,
            },
            "product_options": {
                "enabled_variants": [int(variant_id)],
            },
        }
        if customer_name:
            attributes["checkout_data"]["name"] = customer_name
        if redirect_url:
            attributes["product_options"]["redirect_url"] = redirect_url
        if quantity and quantity > 1:
            # Quantity-based price: Lemon Squeezy 는 checkout 시점에 fixed.
            # 가입 후 update_subscription_quantity 로 조정.
            attributes["checkout_options"]["quantity"] = int(quantity)
        body = {
            "data": {
                "type": "checkouts",
                "attributes": attributes,
                "relationships": {
                    "store": {"data": {"type": "stores", "id": str(self.store_id)}},
                    "variant": {"data": {"type": "variants", "id": str(variant_id)}},
                },
            }
        }
        result = self._request("POST", "/checkouts", json=body)
        try:
            return result["data"]["attributes"]["url"]
        except (KeyError, TypeError) as e:
            raise LemonSqueezyError(f"checkout response missing url: {result}") from e


# -----------------------------------------------------------------------------
# Webhook signature verification (HMAC-SHA256)
# -----------------------------------------------------------------------------


def verify_webhook_signature(*, raw_body: bytes, signature_header: str, secret: Optional[str] = None) -> bool:
    """Lemon Squeezy webhook 의 `X-Signature` 헤더를 검증.

    - secret: 명시 안 하면 settings.lemon_webhook_secret 사용.
    - raw_body: HTTP 요청 본문 바이트 (re-serialize 하면 안 됨, raw 그대로).
    - signature_header: 「X-Signature」 헤더 값 (hex string).
    Lemon Squeezy doc: HMAC-SHA256(secret, body).hexdigest() 와 비교.
    """
    use_secret = secret if secret is not None else get_settings().lemon_webhook_secret
    if not use_secret or not signature_header:
        return False
    digest = hmac.new(use_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    # Timing-safe 비교
    try:
        return hmac.compare_digest(digest, signature_header.strip().lower())
    except Exception:
        return False


# -----------------------------------------------------------------------------
# Singleton helper (for routes/services that want a quick client)
# -----------------------------------------------------------------------------


def get_client() -> LemonSqueezyClient:
    """단순 헬퍼. 매 호출마다 새 인스턴스 (httpx.Client 는 안에서 즉시 닫힘)."""
    return LemonSqueezyClient()
