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


def translate_ja_to_target(text: str, target_locale: str, settings: "Settings") -> str:
    """일본어 원문을 target_locale 으로 번역. ja 이면 그대로."""
    t = (text or "").strip()
    if not t:
        return ""
    tgt = TARGET_TO_GOOGLE.get(target_locale, target_locale)
    if tgt == "ja":
        return t
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
    return str(trs[0].get("translatedText", t) or t)
