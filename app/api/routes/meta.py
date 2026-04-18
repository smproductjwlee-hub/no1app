from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.config import LOCALE_DISPLAY_NAMES, Settings, get_settings

router = APIRouter(prefix="/meta", tags=["meta"])


@router.get("/locale-config")
async def locale_config(settings: Settings = Depends(get_settings)) -> dict:
    """직원 UI용: 표시 언어 탭 순서(설정: ja,en,vi,id,my,ne 등)."""
    targets = []
    for loc in settings.translation_target_locales_ordered():
        targets.append(
            {
                "locale": loc,
                "label": LOCALE_DISPLAY_NAMES.get(loc, loc.upper()),
            },
        )
    return {
        "source_locale": settings.translation_source_locale,
        "source_label": LOCALE_DISPLAY_NAMES.get(settings.translation_source_locale, "ja"),
        "targets": targets,
    }
