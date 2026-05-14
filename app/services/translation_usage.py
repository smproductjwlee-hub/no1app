"""ワークスペース別の翻訳 API 使用量トラッキング (Phase 1.5 オプションA)。

各ワークスペースの月別: API 実呼び出し字数, キャッシュヒット字数, 呼び出し回数。
super.html の請求レポートが当該月のマージン (SaaS料金 - API実コスト) を表示するために使う。

Phase 2.10 — 자동 플랜 업그레이드 (계약서 §5.5):
  Starter (50K chars/月) / Business (200K chars/月) / Enterprise (1M chars/月)
  당월 api_chars 가 플랜 상한 초과 시 workspaces.assigned_plan 을 자동 상향.
  plan_upgrade_events 에 감사 추적 기록. 한 워크스페이스는 한 month·plan 조합에 대해
  중복 업그레이드 안 함.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.db.sqlite import get_connection


# Google Translate v2 単価: $20 / 1M chars. 為替 ¥150/$ で計算 (簡易).
# 実コストは GCP コンソールで確認できる。ここはあくまで推定 (請求レポート表示用).
USD_PER_M_CHARS = 20.0
JPY_PER_USD = 150.0
JPY_PER_M_CHARS = USD_PER_M_CHARS * JPY_PER_USD  # = ¥3,000 / 1M chars

# Phase 2.10 — 플랜별 월간 API 문자 상한 (계약서 §5.1.1)
PLAN_LIMITS = {
    "starter": 50_000,        # 5万字
    "business": 200_000,       # 20万字
    "enterprise": 1_000_000,    # 100万字 (이 이상은 자동 업그레이드 없음, 경고만)
}
PLAN_ORDER = ["starter", "business", "enterprise"]


def _next_plan(current: str) -> Optional[str]:
    """현재 플랜의 다음 단계. enterprise 는 더 이상 없음."""
    try:
        i = PLAN_ORDER.index(current)
    except ValueError:
        return None
    return PLAN_ORDER[i + 1] if i + 1 < len(PLAN_ORDER) else None


def estimate_jpy_cost(api_chars: int) -> int:
    """API 字数から円コスト推定 (整数円, 切上げ)."""
    if not api_chars or api_chars <= 0:
        return 0
    yen = api_chars * JPY_PER_M_CHARS / 1_000_000
    return int(yen + 0.999)  # 切り上げ


def _ym_now_utc() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m")


def _ym_for_year_month(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def record_api_call(workspace_id: Optional[str], chars: int) -> None:
    """API を実際に呼んだ際に記録 (キャッシュ未ヒット).

    記録後、当月使用量が現プラン上限超過なら 자동 플랜 업그레이드를 시도한다.
    실패해도 본 함수는 silent 처리 (API 호출 본 흐름에 영향 X).
    """
    if not workspace_id or chars <= 0:
        return
    conn = get_connection()
    ym = _ym_now_utc()
    now = time.time()
    try:
        conn.execute(
            """
            INSERT INTO translation_usage
            (workspace_id, year_month, api_chars, cached_chars, api_calls, cache_hits, last_updated_at)
            VALUES (?, ?, ?, 0, 1, 0, ?)
            ON CONFLICT(workspace_id, year_month) DO UPDATE SET
              api_chars = translation_usage.api_chars + excluded.api_chars,
              api_calls = translation_usage.api_calls + 1,
              last_updated_at = excluded.last_updated_at
            """,
            (workspace_id, ym, int(chars), now),
        )
        conn.commit()
    except Exception:
        # 計測層は静かに失敗 (本筋に影響しない)
        try:
            conn.rollback()
        except Exception:
            pass
        return  # 기록 실패 시 업그레이드 체크 skip
    # 자동 플랜 업그레이드 체크 (silent fail)
    try:
        _check_and_upgrade_plan(workspace_id, ym)
    except Exception:
        pass


def _check_and_upgrade_plan(workspace_id: str, ym: str) -> Optional[dict]:
    """당월 api_chars 가 현 플랜 상한 초과 시 자동으로 다음 플랜으로 업그레이드.

    한 ws · ym · from_plan 조합에 대해 중복 업그레이드 안 함 (멱등성).
    업그레이드 발생 시 plan_upgrade_events 에 기록하고 dict 반환.
    아니면 None.
    """
    conn = get_connection()
    # 현재 워크스페이스 정보 + 당월 api_chars
    ws_row = conn.execute(
        "SELECT id, assigned_plan, distributor_id FROM workspaces WHERE id = ?",
        (workspace_id,),
    ).fetchone()
    if ws_row is None:
        return None
    current = (ws_row["assigned_plan"] or "starter").lower()
    if current not in PLAN_LIMITS:
        return None
    nxt = _next_plan(current)
    if nxt is None:
        return None  # Enterprise 는 자동 업그레이드 없음
    limit = PLAN_LIMITS[current]
    u_row = conn.execute(
        "SELECT api_chars FROM translation_usage WHERE workspace_id = ? AND year_month = ?",
        (workspace_id, ym),
    ).fetchone()
    api_chars = int(u_row["api_chars"] or 0) if u_row else 0
    if api_chars <= limit:
        return None
    # 이미 같은 ym 에서 같은 from_plan 으로 업그레이드한 적 있으면 skip (멱등)
    dup = conn.execute(
        "SELECT id FROM plan_upgrade_events WHERE workspace_id = ? AND year_month = ? AND from_plan = ?",
        (workspace_id, ym, current),
    ).fetchone()
    if dup is not None:
        return None
    # 플랜 상향
    conn.execute(
        "UPDATE workspaces SET assigned_plan = ? WHERE id = ?",
        (nxt, workspace_id),
    )
    event_id = str(uuid.uuid4())
    now = time.time()
    conn.execute(
        """
        INSERT INTO plan_upgrade_events
        (id, workspace_id, distributor_id, year_month, from_plan, to_plan,
         triggered_api_chars, threshold, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id, workspace_id,
            ws_row["distributor_id"] or "",
            ym, current, nxt,
            api_chars, limit, now,
        ),
    )
    conn.commit()
    return {
        "id": event_id,
        "workspace_id": workspace_id,
        "year_month": ym,
        "from_plan": current,
        "to_plan": nxt,
        "triggered_api_chars": api_chars,
        "threshold": limit,
    }


def list_upgrade_events(
    limit: int = 50,
    distributor_id: Optional[str] = None,
) -> list[dict]:
    """최근 자동 업그레이드 이벤트 일람 (시간 역순).

    distributor_id 지정 시 해당 대리점 산하만 필터.
    """
    conn = get_connection()
    if distributor_id:
        rows = conn.execute(
            """
            SELECT id, workspace_id, distributor_id, year_month,
                   from_plan, to_plan, triggered_api_chars, threshold, created_at
            FROM plan_upgrade_events
            WHERE distributor_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (distributor_id, int(limit)),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, workspace_id, distributor_id, year_month,
                   from_plan, to_plan, triggered_api_chars, threshold, created_at
            FROM plan_upgrade_events
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "workspace_id": r["workspace_id"],
            "distributor_id": r["distributor_id"] or "",
            "year_month": r["year_month"],
            "from_plan": r["from_plan"],
            "to_plan": r["to_plan"],
            "triggered_api_chars": int(r["triggered_api_chars"] or 0),
            "threshold": int(r["threshold"] or 0),
            "created_at": float(r["created_at"] or 0),
        })
    return out


def record_cache_hit(workspace_id: Optional[str], chars: int) -> None:
    """キャッシュヒットで API を呼ばずに済んだ際に記録."""
    if not workspace_id or chars <= 0:
        return
    conn = get_connection()
    ym = _ym_now_utc()
    now = time.time()
    try:
        conn.execute(
            """
            INSERT INTO translation_usage
            (workspace_id, year_month, api_chars, cached_chars, api_calls, cache_hits, last_updated_at)
            VALUES (?, ?, 0, ?, 0, 1, ?)
            ON CONFLICT(workspace_id, year_month) DO UPDATE SET
              cached_chars = translation_usage.cached_chars + excluded.cached_chars,
              cache_hits = translation_usage.cache_hits + 1,
              last_updated_at = excluded.last_updated_at
            """,
            (workspace_id, ym, int(chars), now),
        )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


def get_usage(workspace_id: str, year: int, month: int) -> dict:
    """指定ワークスペースの指定月の使用量。データ無しなら全 0."""
    if not workspace_id:
        return {"api_chars": 0, "cached_chars": 0, "api_calls": 0, "cache_hits": 0}
    ym = _ym_for_year_month(year, month)
    conn = get_connection()
    row = conn.execute(
        "SELECT api_chars, cached_chars, api_calls, cache_hits FROM translation_usage "
        "WHERE workspace_id = ? AND year_month = ?",
        (workspace_id, ym),
    ).fetchone()
    if row is None:
        return {"api_chars": 0, "cached_chars": 0, "api_calls": 0, "cache_hits": 0}
    return {
        "api_chars": int(row["api_chars"] or 0),
        "cached_chars": int(row["cached_chars"] or 0),
        "api_calls": int(row["api_calls"] or 0),
        "cache_hits": int(row["cache_hits"] or 0),
    }


def get_usage_map_for_month(year: int, month: int) -> dict[str, dict]:
    """指定月の全ワークスペース使用量 dict[workspace_id] -> stats."""
    ym = _ym_for_year_month(year, month)
    conn = get_connection()
    rows = conn.execute(
        "SELECT workspace_id, api_chars, cached_chars, api_calls, cache_hits "
        "FROM translation_usage WHERE year_month = ?",
        (ym,),
    ).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        out[r["workspace_id"]] = {
            "api_chars": int(r["api_chars"] or 0),
            "cached_chars": int(r["cached_chars"] or 0),
            "api_calls": int(r["api_calls"] or 0),
            "cache_hits": int(r["cache_hits"] or 0),
        }
    return out
