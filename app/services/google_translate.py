"""Google Cloud Translation API v2 — 일본어 원문만 입력, 대상 언어만 번역."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from googleapiclient.discovery import build

from app.services.gcp_auth import credentials_translate
from app.services.google_sheets import resolve_credentials_path

if TYPE_CHECKING:
    from app.core.config import Settings

# worker / meta 와 동일한 locale 코드 → Translation API language code
TARGET_TO_GOOGLE: dict[str, str] = {
    "ja": "ja",
    "en": "en",
    "vi": "vi",
    "id": "id",
    "my": "my",
    "ne": "ne",
}


@lru_cache(maxsize=4)
def _translate_service(
    use_adc: bool,
    key_path: str,
    imp_email: str,
):
    creds = credentials_translate(
        use_adc_impersonate=use_adc,
        key_file=key_path if not use_adc else "",
        impersonate_service_account=imp_email if use_adc else "",
    )
    return build("translate", "v2", credentials=creds, cache_discovery=False)


def get_translate_service(settings: "Settings"):
    if settings.google_use_adc_impersonate:
        return _translate_service(
            True,
            "",
            (settings.google_impersonate_service_account or "").strip(),
        )
    resolved = str(resolve_credentials_path(settings.google_credentials_path))
    return _translate_service(False, resolved, "")


def translate_ja_to_target(
    text: str,
    target_locale: str,
    settings: "Settings",
    workspace_id: "str | None" = None,
) -> str:
    """일본어 원문을 target_locale 으로 번역. ja 이면 그대로.
    SQLite translation_cache 를 통해 이미 번역된 표현은 API 호출 없이 즉시 반환.

    workspace_id が渡されると、ワークスペース別の API 使用量を集計する
    (translation_usage テーブル, 請求レポートに使う)。
    """
    t = (text or "").strip()
    if not t:
        return ""
    tgt = TARGET_TO_GOOGLE.get(target_locale, target_locale)
    if tgt == "ja":
        return t
    char_count = len(t)
    # 캐시 lookup
    from app.services.translation_cache import get_translation, store_translation
    from app.services import translation_usage as _usage
    try:
        cached = get_translation(t, tgt)
        if cached is not None:
            try:
                _usage.record_cache_hit(workspace_id, char_count)
            except Exception:
                pass
            return cached
    except Exception:
        pass
    svc = get_translate_service(settings)
    resp = (
        svc.translations()
        .translate(
            body={
                "q": t,
                "source": "ja",
                "target": tgt,
                "format": "text",
            }
        )
        .execute()
    )
    trs = resp.get("translations") or []
    if not trs:
        return t
    out = str(trs[0].get("translatedText", t) or t)
    # API 実呼び出し記録 (workspace_id 別)
    try:
        _usage.record_api_call(workspace_id, char_count)
    except Exception:
        pass
    try:
        store_translation(t, tgt, out)
    except Exception:
        pass
    return out
