"""Lemon Squeezy 설정 smoke test.

사용법:
    # 1. .env 에 LEMON_API_KEY, LEMON_STORE_ID 등 설정
    # 2. 다음 명령 실행:
    PYTHONIOENCODING=utf-8 python scripts/lemon_smoke.py

확인 항목:
    1. API key 가 Lemon Squeezy 에 의해 인정됨 (/users/me)
    2. Store ID 가 유효함 (/stores/{id})
    3. Variant ID 4 종이 모두 실재함 (Starter/Business/Enterprise/MVP)
    4. Webhook secret 가 설정되어 있는지

이 스크립트는 실제 결제를 발생시키지 않습니다 — 읽기 전용 호출만.
"""

from __future__ import annotations

import os
import sys

# 프로젝트 루트를 import 경로에 추가 (스크립트 직접 실행 시)
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.core.config import get_settings  # noqa: E402
from app.services.billing.lemon_squeezy import (  # noqa: E402
    LemonSqueezyClient,
    LemonSqueezyError,
    is_configured,
    is_fully_configured,
)


def main() -> int:
    s = get_settings()

    if not is_configured():
        print("✗ LEMON_API_KEY / LEMON_STORE_ID 가 비어 있습니다.")
        print("  .env 파일에 다음을 추가하세요:")
        print("    LEMON_API_KEY=ey...")
        print("    LEMON_STORE_ID=12345")
        return 1

    print(f"== Phase 3.1 Lemon Squeezy smoke test ==")
    print(f"  test_mode: {s.lemon_test_mode}")
    print(f"  store_id : {s.lemon_store_id}")
    print()

    try:
        client = LemonSqueezyClient()
    except LemonSqueezyError as e:
        print(f"✗ クライアント初期化失敗: {e}")
        return 1

    # 1. /users/me
    try:
        me = client.whoami()
        attrs = me.get("data", {}).get("attributes", {}) or {}
        print(f"✓ API key valid — account: {attrs.get('email', '(no email in response)')}")
    except LemonSqueezyError as e:
        print(f"✗ /users/me 실패: {e}")
        return 1

    # 2. /stores/{id}
    try:
        store = client.get_store()
        attrs = store.get("data", {}).get("attributes", {}) or {}
        print(f"✓ Store: {attrs.get('name', '?')}  (currency={attrs.get('currency', '?')})")
    except LemonSqueezyError as e:
        print(f"✗ /stores/{s.lemon_store_id} 실패: {e}")
        return 1

    # 3. Variants
    variant_map = {
        "Starter":    s.lemon_variant_starter,
        "Business":   s.lemon_variant_business,
        "Enterprise": s.lemon_variant_enterprise,
        "MVP Fee":    s.lemon_variant_mvp_fee,
    }
    missing_in_env = [k for k, v in variant_map.items() if not v]
    if missing_in_env:
        print(f"⚠ 다음 variant ID 가 .env 에 비어 있음: {missing_in_env}")
        print(f"  (Phase 3.3 부터 필요. 지금은 client 자체는 OK)")
    else:
        try:
            all_variants = client.list_variants(page_size=100)
            v_ids = {str(v.get("id")) for v in all_variants}
            print(f"✓ Store 의 variant 수: {len(all_variants)}")
            for label, v_id in variant_map.items():
                if str(v_id) in v_ids:
                    # variant 의 이름·가격 같이 출력
                    match = next((v for v in all_variants if str(v.get("id")) == str(v_id)), None)
                    a = (match or {}).get("attributes", {}) or {}
                    print(
                        f"  ✓ {label:11s} id={v_id}  name={a.get('name', '?')}  "
                        f"price={a.get('price_formatted', '?')}"
                    )
                else:
                    print(f"  ✗ {label} id={v_id} — store 에 존재하지 않음")
        except LemonSqueezyError as e:
            print(f"✗ /variants 실패: {e}")
            return 1

    # 4. Webhook secret 설정 여부
    if s.lemon_webhook_secret:
        print(f"✓ Webhook secret 설정됨 ({len(s.lemon_webhook_secret)} chars)")
    else:
        print(f"⚠ LEMON_WEBHOOK_SECRET 미설정 — Phase 3.4 부터 필요")

    print()
    if is_fully_configured():
        print("✅ All checks passed. Phase 3.3 (Checkout 흐름) 으로 진행 가능.")
    else:
        print("ℹ️ Phase 3.1 의 client 는 동작. 나머지 variant/secret 채워야 3.3+ 진행 가능.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
