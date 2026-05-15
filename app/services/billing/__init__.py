"""Phase 3 — 결제 / 청구 서비스 (Lemon Squeezy 구독 모델).

대리점이 본인 (운영자) 에게 매월 도매가 만큼을 자동 결제하는 구조.
- 결제 게이트웨이: Lemon Squeezy (Merchant of Record — 일본 소비세 자동 처리)
- 모델: Subscription + Quantity (워크스페이스 수 = quantity)
- MVP 開発費: One-time charge (별도 결제 링크)

이 패키지의 단위:
- lemon_squeezy.py — API 클라이언트 + webhook 서명 검증
- (Phase 3.3+): checkout 흐름·webhook 핸들러·DB 영속화
"""
