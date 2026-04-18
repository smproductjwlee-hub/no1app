from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.services.easy_japanese import build_easy_japanese
from app.services.google_translate import translate_ja_to_target
from app.services.stores import Role, sessions

router = APIRouter(prefix="/i18n", tags=["i18n"])


class TranslateIn(BaseModel):
    text: str = Field(..., max_length=8000)
    target_locale: str = Field(..., min_length=2, max_length=12)


class TranslateOut(BaseModel):
    translated_text: str


class EasyJaIn(BaseModel):
    text: str = Field(..., max_length=8000)


class EasyJaOut(BaseModel):
    easy_ja: str


def _require_worker(token: str) -> None:
    sess = sessions.get(token)
    if sess is None or sess.role != Role.WORKER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="worker session required",
        )


@router.post("/translate", response_model=TranslateOut)
async def translate_for_worker(
    body: TranslateIn,
    token: str = Query(..., min_length=8),
    settings: Settings = Depends(get_settings),
) -> TranslateOut:
    """언어 탭 선택 시에만 호출: 일본어 원문 → 해당 언어."""
    _require_worker(token)
    try:
        out = translate_ja_to_target(body.text, body.target_locale, settings)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Translation failed: {exc}",
        ) from exc
    return TranslateOut(translated_text=out)


@router.post("/easy-ja", response_model=EasyJaOut)
async def easy_japanese_for_worker(
    body: EasyJaIn,
    token: str = Query(..., min_length=8),
    settings: Settings = Depends(get_settings),
) -> EasyJaOut:
    """분야·매장 용어 시트를 참고한やさしい日本語(치환)."""
    _require_worker(token)
    try:
        out = build_easy_japanese(body.text, settings)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Easy Japanese failed: {exc}",
        ) from exc
    return EasyJaOut(easy_ja=out)
