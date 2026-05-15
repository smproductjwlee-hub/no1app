from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# 근로자용 번역 문구 표시 이름 (locale → 라벨). 우선순위는 Settings.translation_target_locale_order.
LOCALE_DISPLAY_NAMES: dict[str, str] = {
    "ja": "日本語",
    "vi": "Tiếng Việt",
    "en": "US English",
    "id": "Bahasa Indonesia",
    "my": "မြန်မာဘာသာ",
    "ne": "नेपाली",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "WorkBridge Japan API"
    debug: bool = False
    # ワークスペース永続化: sqlite:/// のローカルファイル（追加ライブラリ不要）
    database_url: str = "sqlite:///./data/workbridge.db"
    # Base URL for QR / join links (set in production, e.g. https://app.example.com)
    public_base_url: str = "http://127.0.0.1:8000"

    # Session token TTL (seconds)
    session_token_ttl_seconds: int = 86400

    # 指示・応答履歴の保管期間 (日). 教材作成・分析のため店長↔スタッフのやりとりを
    # 一定期間残す。最低 6 ヶ月 (180 日) を推奨. .env で延長可能 (例: 365 = 1年).
    instruction_retention_days: int = 180

    # ポータルログイン（MVP: .env で本番用に変更）。スタッフは個人アカウント必須。
    portal_admin_password: str = "admin"
    # 運営会社スーパー管理者（全顧客ワークスペース閲覧用）
    super_admin_password: str = "superadmin"
    # セッショントークン（JWT）の署名鍵。本番では必ず長くランダムな値を .env に設定する。
    # 鍵が変わると既存の発行済みトークンは全て無効になる（=全ユーザーが再ログイン）。
    session_secret: str = "dev-only-change-me-in-production-please-3xKp9wL"

    # Google Sheets: (A) json 키 파일 or (B) 로컬 ADC + 서비스 계정 가장(키 발급 막힌 조직용)
    google_use_adc_impersonate: bool = False
    google_impersonate_service_account: str = ""  # 예: workjapan@workbridge-japan.iam.gserviceaccount.com
    google_credentials_path: str = "google_key.json"
    kaigo_spreadsheet_id: str = "1dvXCqM8Zex0Zn0-z5J_rsaM7FgnFiv4hndbs096ZdAg"
    food_spreadsheet_id: str = "1TWDvD48SBF0hXWKH7vosjiyuX_YScgchjbW7rJPnoPE"
    # Tab identifier as shown in URL ?gid=
    kaigo_sheet_gid: int = 1664075435
    food_sheet_gid: int = 0
    # 음식업계 분야별 단어집 — 엑셀 업로드본 대신 네이티브 시트로 복사해 둔 문서
    food_glossary_spreadsheet_id: str = "1b6rqAVZpo_qj2EjrrQyqKNESSDrqa75z7FVK60s0ruI"
    food_glossary_sheet_gid: int = 838759916
    # 세 번째 시트(구 엑셀 업로드 → 새 Google 시트로 재작성 한 문서, gid=0 탭)
    extra_spreadsheet_id: str = "1sTgBJHhojdAmINcR7NpT6Veymj4YuFGXtCMlCxsWRF4"
    extra_sheet_gid: int = 0
    # 매장·현장별 용어(やさしい日本語 치환) — 비우면 food-glossary·extra 만 사용
    site_glossary_spreadsheet_id: str = ""
    site_glossary_sheet_gid: int = 0
    site_glossary_header_row: int = 1
    # 용어 시트 메모리 캐시 TTL (초)
    glossary_cache_ttl_seconds: int = 300
    # Row number (1-based) that contains column headers
    kaigo_header_row: int = 5
    food_header_row: int = 4
    food_glossary_header_row: int = 1
    extra_header_row: int = 5
    # 日本語コース一覧_東南アジア向け — 엑셀 업로드 대신 네이티브 시트로 재작성
    course_list_spreadsheet_id: str = "1_AodcrMYhURbxKlN686gE6OI9x5ecuMtSBK7UFpa68U"
    course_list_sheet_gid: int = 0
    course_list_header_row: int = 1

    # 번역 파이프라인 (기획): 원문은 관리자 일본어 · 근로자 표시 언어 탭 순서
    translation_source_locale: str = "ja"
    # 쉼표 구분: JP, US, VN, ID, MM, NP (locale 코드)
    translation_target_locale_order: str = "ja,en,vi,id,my,ne"

    def translation_target_locales_ordered(self) -> list[str]:
        return [x.strip() for x in self.translation_target_locale_order.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
