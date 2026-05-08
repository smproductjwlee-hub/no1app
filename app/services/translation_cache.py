"""번역(translate) · やさしい日本語(easy-ja) 결과의 영구 캐시.

같은 일본어 원문 → 같은 결과가 나오므로 워크스페이스 무관하게 전역 공유 가능.
번역 API 호출 비용을 50~70% 절감하는 것이 목표.
"""

from __future__ import annotations

import hashlib
import time
from typing import Iterable, Optional

from app.db.sqlite import get_connection


# ----- Google Translate cache -----


def get_translation(source_text: str, target_locale: str) -> Optional[str]:
    """캐시 히트 시 번역 결과를 반환하고 last_used_at·hit_count를 갱신.
    캐시 미스면 None.
    """
    if not source_text or not target_locale:
        return None
    conn = get_connection()
    row = conn.execute(
        """
        SELECT translated_text FROM translation_cache
        WHERE source_text = ? AND target_locale = ?
        """,
        (source_text, target_locale),
    ).fetchone()
    if row is None:
        return None
    now = time.time()
    conn.execute(
        """
        UPDATE translation_cache
        SET last_used_at = ?, hit_count = hit_count + 1
        WHERE source_text = ? AND target_locale = ?
        """,
        (now, source_text, target_locale),
    )
    conn.commit()
    return str(row["translated_text"])


def store_translation(source_text: str, target_locale: str, translated_text: str) -> None:
    if not source_text or not target_locale or not translated_text:
        return
    conn = get_connection()
    now = time.time()
    conn.execute(
        """
        INSERT INTO translation_cache
        (source_text, target_locale, translated_text, created_at, last_used_at, hit_count)
        VALUES (?, ?, ?, ?, ?, 1)
        ON CONFLICT(source_text, target_locale) DO UPDATE SET
          translated_text = excluded.translated_text,
          last_used_at = excluded.last_used_at
        """,
        (source_text, target_locale, translated_text, now, now),
    )
    conn.commit()


# ----- Easy Japanese cache -----


def glossary_version(pairs: Iterable[tuple[str, str]]) -> str:
    """글로서리 페어 목록의 결정적 해시. 같은 페어 → 같은 키.
    페어 순서는 길이 내림차순으로 _merge_pairs에서 정렬되므로 안정적.
    """
    h = hashlib.sha256()
    for term, easy in pairs:
        h.update(b"\x1e")  # record separator
        h.update(term.encode("utf-8"))
        h.update(b"\x1f")  # unit separator
        h.update(easy.encode("utf-8"))
    return h.hexdigest()[:24]


def get_easy_ja(source_text: str, glossary_ver: str) -> Optional[str]:
    if not source_text or not glossary_ver:
        return None
    conn = get_connection()
    row = conn.execute(
        """
        SELECT easy_text FROM easy_ja_cache
        WHERE source_text = ? AND glossary_version = ?
        """,
        (source_text, glossary_ver),
    ).fetchone()
    if row is None:
        return None
    now = time.time()
    conn.execute(
        """
        UPDATE easy_ja_cache
        SET last_used_at = ?, hit_count = hit_count + 1
        WHERE source_text = ? AND glossary_version = ?
        """,
        (now, source_text, glossary_ver),
    )
    conn.commit()
    return str(row["easy_text"])


def store_easy_ja(source_text: str, glossary_ver: str, easy_text: str) -> None:
    if not source_text or not glossary_ver or not easy_text:
        return
    conn = get_connection()
    now = time.time()
    conn.execute(
        """
        INSERT INTO easy_ja_cache
        (source_text, glossary_version, easy_text, created_at, last_used_at, hit_count)
        VALUES (?, ?, ?, ?, ?, 1)
        ON CONFLICT(source_text, glossary_version) DO UPDATE SET
          easy_text = excluded.easy_text,
          last_used_at = excluded.last_used_at
        """,
        (source_text, glossary_ver, easy_text, now, now),
    )
    conn.commit()


# ----- Cleanup (optional, schedule manually) -----


def cleanup_stale_easy_ja(keep_versions: int = 4) -> int:
    """가장 최근에 쓰인 N개의 glossary_version만 남기고 나머지 삭제.
    글로서리가 자주 바뀌어 누적된 옛 버전 항목을 정리할 때 사용.
    """
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT glossary_version
        FROM easy_ja_cache
        GROUP BY glossary_version
        ORDER BY MAX(last_used_at) DESC
        LIMIT 100
        """
    ).fetchall()
    keep = {r["glossary_version"] for r in rows[:keep_versions]}
    if not keep:
        return 0
    placeholders = ",".join("?" for _ in keep)
    cursor = conn.execute(
        f"DELETE FROM easy_ja_cache WHERE glossary_version NOT IN ({placeholders})",
        tuple(keep),
    )
    conn.commit()
    return cursor.rowcount or 0


# ----- Stats (운영용) -----


def stats() -> dict:
    conn = get_connection()
    tr_count = conn.execute("SELECT COUNT(*) AS c FROM translation_cache").fetchone()["c"]
    tr_hits = conn.execute("SELECT COALESCE(SUM(hit_count), 0) AS s FROM translation_cache").fetchone()["s"]
    ej_count = conn.execute("SELECT COUNT(*) AS c FROM easy_ja_cache").fetchone()["c"]
    ej_hits = conn.execute("SELECT COALESCE(SUM(hit_count), 0) AS s FROM easy_ja_cache").fetchone()["s"]
    return {
        "translation_entries": int(tr_count),
        "translation_total_hits": int(tr_hits),
        "easy_ja_entries": int(ej_count),
        "easy_ja_total_hits": int(ej_hits),
    }
