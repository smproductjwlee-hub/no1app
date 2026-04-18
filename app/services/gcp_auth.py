"""
Google API 자격 증명: (1) 서비스 계정 키 파일, (2) gcloud application-default + 서비스 계정 가장(impersonation).

로컬(2): 터미널에서 먼저
  gcloud auth application-default login
본인 계정에 target SA에 대해 roles/iam.serviceAccountTokenCreator 등이 있어야 함.
"""

from __future__ import annotations

import google.auth
from google.auth import impersonated_credentials
from google.auth.credentials import Credentials
from google.oauth2 import service_account

# Sheets: 읽기. 가장(impersonation) 시 target_scopes에 필요.
SHEETS_READONLY = ("https://www.googleapis.com/auth/spreadsheets.readonly",)
# User ADC(소스) 쪽. 가장 토큰 발급에 쓰임.
CLOUD_PLATFORM = "https://www.googleapis.com/auth/cloud-platform"


def credentials_sheets(
    *,
    use_adc_impersonate: bool,
    key_file: str,
    impersonate_service_account: str,
) -> Credentials:
    if use_adc_impersonate:
        sa = (impersonate_service_account or "").strip()
        if not sa:
            msg = "google_use_adc_impersonate=True 인데 google_impersonate_service_account 가 비어 있습니다."
            raise ValueError(msg)
        # 소스: 로컬 ADC (gcloud auth application-default login)
        source, _ = google.auth.default(scopes=(CLOUD_PLATFORM,))
        return impersonated_credentials.Credentials(
            source_credentials=source,
            target_principal=sa,
            target_scopes=list(SHEETS_READONLY),
        )
    p = (key_file or "").strip()
    if not p:
        raise ValueError("google_credentials_path(키 파일 경로)가 비어 있습니다.")
    return service_account.Credentials.from_service_account_file(
        p,
        scopes=list(SHEETS_READONLY),
    )
