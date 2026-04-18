"""
분야용 단어 시트(food-glossary 등) + 매장(현장)용 시트를 합쳐,
原文에 등장하는 용어를 시트의「やさしい日本語」열 치환으로 단순화.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING

from app.services.google_sheets import (
    fetch_sheet_grid,
    get_sheets_service,
    grid_to_records,
)

if TYPE_CHECKING:
    from app.core.config import Settings

# 시트 열 이름 휴리스틱 (일본 프로젝트)
_TERM_PATTERNS = (
    "日本語",
    "用語",
    "単語",
    "原文",
    "語",
    "カタカナ",
    "表記",
)
_EASY_PATTERNS = (
    "やさしい",
    "易しい",
    "簡単",
    "かんたん",
    "言い換え",
    "置換",
    "置き換え",
    "やさしい日本語",
)


def _pair_from_row(row: dict[str, str]) -> tuple[str, str]:
    keys = [k for k in row.keys() if (k or "").strip()]
    term = ""
    easy = ""
    for k in keys:
        kl = k.lower()
        if any(p in k for p in _TERM_PATTERNS) and not re.search(r"意味|説明|例|한국|英語|vi|id", k, re.I):
            v = (row.get(k) or "").strip()
            if v:
                term = v
                break
    if not term:
        for k in keys:
            v = (row.get(k) or "").strip()
            if v:
                term = v
                break
    for k in keys:
        if any(p in k for p in _EASY_PATTERNS):
            v = (row.get(k) or "").strip()
            if v:
                easy = v
                break
    return term, easy


def _merge_pairs(settings: "Settings") -> list[tuple[str, str]]:
    service = get_sheets_service(settings)
    pairs: list[tuple[str, str]] = []
    # 분야별 단어: food-glossary（개호 시나리오 시트는 시나리오 위주라 기본 제외）
    specs: list[tuple[str, int, int]] = [
        (
            settings.food_glossary_spreadsheet_id,
            settings.food_glossary_sheet_gid,
            settings.food_glossary_header_row,
        ),
    ]
    site_id = (settings.site_glossary_spreadsheet_id or "").strip()
    if site_id:
        specs.append(
            (
                site_id,
                settings.site_glossary_sheet_gid,
                settings.site_glossary_header_row,
            )
        )
    for sid, gid, hrow in specs:
        if not sid:
            continue
        try:
            grid = fetch_sheet_grid(service, sid, gid)
            rows = grid_to_records(grid, hrow)
        except Exception:
            continue
        for r in rows:
            term, easy = _pair_from_row(r)
            if term and easy and term != easy:
                pairs.append((term, easy))
    pairs.sort(key=lambda x: len(x[0]), reverse=True)
    return pairs


_glossary_pairs: list[tuple[str, str]] | None = None
_glossary_at: float = 0.0
_TTL = 300.0


def _cache_ttl_seconds(settings: "Settings") -> float:
    return float(settings.glossary_cache_ttl_seconds)


def build_easy_japanese(text: str, settings: "Settings") -> str:
    """用語シート 기반으로 치환. 실패 시 원문 유지."""
    global _glossary_pairs, _glossary_at
    t = (text or "").strip()
    if not t:
        return ""
    ttl = _cache_ttl_seconds(settings)
    if (
        _glossary_pairs is None
        or time.time() - _glossary_at > ttl
    ):
        try:
            _glossary_pairs = _merge_pairs(settings)
        except Exception:
            _glossary_pairs = []
        _glossary_at = time.time()
    pairs = _glossary_pairs or []
    out = t
    for term, easy in pairs:
        if len(term) < 2:
            continue
        if term in out:
            out = out.replace(term, easy)
    return out if out.strip() else t
