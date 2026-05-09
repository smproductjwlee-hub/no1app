"""ワークスペース別の翻訳 API 使用量トラッキング (Phase 1.5 オプションA)。

各ワークスペースの月別: API 実呼び出し字数, キャッシュヒット字数, 呼び出し回数。
super.html の請求レポートが当該月のマージン (SaaS料金 - API実コスト) を表示するために使う。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

from app.db.sqlite import get_connection


# Google Translate v2 単価: $20 / 1M chars. 為替 ¥150/$ で計算 (簡易).
# 実コストは GCP コンソールで確認できる。ここはあくまで推定 (請求レポート表示用).
USD_PER_M_CHARS = 20.0
JPY_PER_USD = 150.0
JPY_PER_M_CHARS = USD_PER_M_CHARS * JPY_PER_USD  # = ¥3,000 / 1M chars


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
    """API を実際に呼んだ際に記録 (キャッシュ未ヒット)."""
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
