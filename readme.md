# WorkBridge Japan — 프로젝트 정의서 (v1.7)

일본 현장 외국인 근로자 실시간 소통 및 일본어 학습 통합 플랫폼

## 1. 프로젝트 개요
### 1.1 프로젝트명
- **WorkBridge Japan** (가칭)

### 1.2 필요성 및 목표
- **배경:** 일본 내 외국인 근로자 증가에 따른 언어 장벽(안전사고, 업무 오해) 해소 필요.
- **목표:** 1. 관리자와 근로자 간 실시간 언어 장벽 해소.
  2. 현장 맞춤형 일본어 학습 환경 제공 (개호, 음식업 등).
  3. B2B(관리단체, 기업) 대상의 지속 가능한 SaaS 모델 구축.

---

## 2. 서비스 구성 (2-in-1 플랫폼)

### [서비스 A] 실시간 현장 소통 툴 (Real-time Comm)
- **대상:** 현장 관리자 + 외국인 근로자
- **핵심 기능:** 음성-지시 번역 전송, 3버튼 응답 시스템, 워크스페이스 관리.

### [서비스 B] 일본어 학습 플랫폼 (Learning LMS)
- **대상:** 외국인 근로자
- **핵심 기능:** 업종별 단어/회화 학습, 퀴즈, 포인트 게임화(편의점 쿠폰 연동).

> **데이터 연동:** 현장에서 '모르겠다(NG)' 응답이 많았던 표현은 학습 플랫폼의 복습 콘텐츠로 자동 생성됨.

---

## 3. 핵심 기능 상세 (Phase 1 & 2)

### 3.1 실시간 소통 시스템
1. **STT & 번역 파이프라인:** - 관리자 음성(일본어) → 텍스트 변환 → 쉬운 일본어 교정 → 모국어 번역 전송.
2. **근로자 응답:** [알았습니다(OK)], [다시 말해주세요(Repeat)], [모르겠습니다(NG)] 실시간 대시보드 반영.
3. **지시 전송 대상:** 관리자 화면에서 라디오로 선택한다. **전원**(브로드캐스트), **선택한 그룹만**(`target_group_id`), **선택한 접속 스태프만**(`target_tokens`).  
   - **스태프 그룹:** 워크스페이스 단위로 그룹(폴더)을 만들고, 등록된 **개인 스태프 계정**(`workspace_staff_accounts`)에 `group_id`를 붙인다. API: `GET/POST/PATCH/DELETE /api/v1/workspaces/{workspace_id}/staff-groups`. **그룹 전송**은 세션에 `staff_account_id`가 있는 워커(개인 계정 로그인)만 수신 대상이 된다.  
   - **개인 스태프 계정:** `GET/POST/PATCH/DELETE .../staff-accounts`. 포털 로그인 시 **스태프ID + 개인 비밀번호**를 쓰면 `POST /api/v1/auth/portal-login`이 세션에 `staff_account_id`를 넣는다(공유 비밀번호만 쓰는 입장은 기존처럼 번호·이름 라벨만).
4. **지시·응답 기록(관리자):** 보낸 지시마다 서버가 `instruction_id`(UUID)를 발급하고, WebSocket 지시 본문에 포함한다. 스태프 화면은 버튼 응답 시 같은 ID를 돌려보낸다(`worker_response`에 `instruction_id`). SQLite에 **전송 시점·수신자 스냅샷·버튼별 응답**이 쌓이고, 관리자 화면 **「보낸 지시 기록」**에서 **녹색(OK)·노랑(REPEAT)·빨강(NG)·그 외(CUSTOM)·회색(미응답)** 집계를 보여 주며, 색을 누르면 해당하는 **스태프 표시명**을 모달로 확인할 수 있다. **보관 기간은 약 60일**(1개월 이상을 전제로 `init_db` 및 조회 시 오래된 행 삭제).  
   - **API:** `GET /api/v1/workspaces/{workspace_id}/instruction-history?admin_token=…` (목록·집계), `GET /api/v1/workspaces/{workspace_id}/instruction-history/{instruction_id}?admin_token=…` (이름별 상세).  
   - 실시간 피드(「작업자의 응답」)에는 응답마다 **스태프 표시명**이 붙는다(WebSocket `worker_response`에 `worker_label` 등).
5. **근로자 식별 표시:** 근로자 화면 상단에 **「スタッフ」+ 번호 또는 이름**을 표시. 조인 시 `user_label`이 없으면 워크스페이스별 자동 번호(`No.1`, `No.2` …), 있으면 해당 문자열(이름)을 표시·세션에 저장. (선택) `/worker?join_token=…&user_label=…` 또는 `GET /api/v1/auth/join?token=…&user_label=…` 로 이름 지정. 이후 이름 변경은 DB(향후) 또는 별도 API로 확장 가능.
6. **포털 로그인 (MVP):** `/login` 에서 **管理者 / 現場スタッフ** 선택 후 **ログインID・パスワード**. `POST /api/v1/auth/portal-login` — 관리자는 **ワークスペース名** + `portal_admin_password`(기본 `admin`, `.env`로 변경), 스태프는 **ワークスペースID 또는同名** + `portal_worker_password`(기본 `worker`). 성공 시 `sessionStorage`에 토큰 저장 후 `/admin`·`/worker`로 이동. **メニュー**からログアウトで `/login`へ。管理者メニュー: **QRコード**（参加用）、**用語・単語登録**（説明モーダル）、**ユーザー一覧**（`online-workers`）。
7. **総運営スーパー管理者（運営会社）:** `portal-login` 의 `role: super_admin` + `super_admin_password`(기본 `superadmin`, `.env`의 `SUPER_ADMIN_PASSWORD`). `GET /api/v1/workspaces?super_token=…` 로 전체 워크스페이스 목록, `POST /api/v1/auth/super-assume` 로 선택 워크스페이스의 **관리자 토큰 발급** 후 `/admin` 과 동일 UI. WebSocket 은 슈퍼 토큰으로 연결 불가(반드시 assume 후 관리자 토큰 사용). `/super` 페이지에서 목록·선택.
8. **管理者 UI レイアウト:** メニューで **PCレイアウト**（広い2カラム・用語/追加管理者の説明パネル）と **スマホレイアウト**（従来の狭幅）を切替。`localStorage` 키 `wb_admin_layout`.
9. **현장별 전용 사전:** 3계층 사전(Global / Industry / Site-specific) 적용 및 구글 스프레드시트 연동.
10. **관리자 화면 UI 언어 (`admin_ui_locale`):** 워크스페이스마다 관리자 메뉴·안내·입력 힌트 등의 표시 언어를 저장한다. SQLite `workspaces` 테이블 컬럼 `admin_ui_locale`(기본 `ja`). **허용 코드:** `ja`, `en`, `ko`, `zh`, `vi`, `id` — 브라우저 음성 입력(Web Speech)이 실용적인 언어만 포함(미얀마어 등 제외).
   - **API:** `GET` / `PATCH /api/v1/workspaces/{workspace_id}/org` 요청·응답 본문에 `admin_ui_locale` 포함. `PATCH` 시 다른 조직 필드와 함께 또는 `admin_ui_locale`만 보내도 된다.
   - **프론트:** `static/admin-i18n.js`가 `window.__WB_ADMIN_I18N__`로 6개 로케일 문자열을 제공하고, `GET /static/admin-i18n.js`로 배포된다. `static/admin.html`은 `data-i18n`과 `applyAdminLocale()`으로 즉시 반영한다. 로그인 전 게이트 화면은 `localStorage` 키 `wb_admin_ui_locale`으로 언어를 맞춘다.
   - **切替 UI:** **メニュー → マイ情報**에서 `<select>`로 저장하거나, **마이크 버튼 아래**와 **화면 하단 고정 바**의 6개 국기 버튼을 눌러 즉시 전환한다(화면 갱신 + `wb_admin_ui_locale` 동기화 + `PATCH`로 서버 저장).
11. **근로자 쪽 번역·やさしい日本語 (API):** `POST /api/v1/i18n/translate`(일본어 원문 → 대상 언어), `POST /api/v1/i18n/easy-japanese`(쉬운 일본어 변환) 등 — 근로자 세션(`worker`)에서 사용. 서비스 모듈: `google_translate`, `easy_japanese`(GCP/설정은 `.env`·`google_key.json` 등, 저장소에는 비밀키 미포함).
12. **관리자 표시 언어 탭 순서 (`locale-config`):** `GET /api/v1/meta/locale-config` 가 반환하는 번역 대상 언어 순서와 맞추되, 관리자 UI에 허용된 로케일(`ja`,`en`,`ko`,`zh`,`vi`,`id`)만 국기 버튼으로 표시한다. 세션 성공 후 설정을 다시 불러와 버튼 순서를 동기화한다.
13. **시나리오·용어 (커리큘럼 참조):** Google Sheets를 `GET /api/v1/curriculum/{kaigo|food|food-glossary|course-list|extra}` 로 조회한다. 스프레드시트마다 **`GET .../{영역}-tabs`** 로 시트(탭) 목록과 `default_sheet_gid` 를 받고, 관리자 「シナリオ・用語」에서 상단 카테고리 선택 후 **탭이 2개 이상이면 가로 스크롤 칩**으로 시트를 고른다. 데이터 요청 시 **`?sheet_gid=`** 로 탭을 지정한다. 기본 표시는 **외식 용어(`food-glossary`)** 쪽을 연다.
14. **지점 전용 용어·표현 (SQLite, 구글 시트 비변경):** 테이블 `workspace_glossary_terms`(용어), `workspace_expression_terms`(표현). 관리자가 분야 탭(`sheet_gid`)을 고르고 등록하면 **해당 워크스페이스 DB에만** 저장되며 공용 구글 시트 본문은 바꾸지 않는다. 시트에 이미 있는 머리글·가게 내 중복은 거절. API: `POST /api/v1/workspaces/{id}/glossary-terms`, `.../expression-terms` (쿼리 `admin_token`). 스태프: `GET /api/v1/auth/worker-food-glossary?token=…&sheet_gid=…` — 시트 행 + 지점 용어 + 지점 표현을 병합해 반환.
15. **지시(指示)에 사진 첨부:** 관리자가 텍스트·STT뿐 아니라 **스크린샷·영수증 사진** 등을 보낼 수 있다. `POST /api/v1/workspaces/{workspace_id}/instruction-image?admin_token=…`(multipart)로 저장 후, WebSocket `instruction` 메시지에 **`text` + 선택적 `image_url`** 을 실어 보낸다. SQLite `instruction_rounds`에 `image_url` 컬럼. 정적 경로: `GET /static/uploads/instruction-images/{workspace_id}/{파일}`. 스태프는 메인·未返信·履歴에 이미지를 표시하고, **이미지 전용 지시**는 번역 API 대신 안내 문구를 쓴다(`worker-i18n`).

### 3.2 학습 및 게임화
1. **스마트 플래시카드:** TTS 발음 재생 지원 및 업종별 커리큘럼(1차: 개호, 2차: 음식업).
2. **포인트 리워드:** 학습 성취도에 따라 일본 편의점 쿠폰(giftee API) 교환.

---

## 4. 기술 스택 (Tech Stack)

- **Backend:** Python (FastAPI), WebSockets
- **Frontend:** PWA (HTML/JS/Tailwind CSS)
- **Database:** SQLite (`sqlite3`, MVP — `data/workbridge.db`, §6.2), Google Sheets API. 향후 PostgreSQL(SQLAlchemy) 전환 검토.
- **AI/ML:** OpenAI Whisper (STT), DeepL/Google Translate (번역), Google TTS
- **Environment:** Windows 10 x64, Python 3.9 (32bit)

---

## 5. 개발 로드맵

1. **Phase 1 (MVP):** WebSocket·QR/조인·포털·슈퍼관리자(목록·`super-assume`)·`GET /auth/session`·관리자 PC/모바일 레이아웃·메뉴(QR·用語·ユーザー)·지시 전송(전원·그룹·개별·**이미지 첨부**)·스태프 계정·그룹(폴더)·`online-workers`·근로자 표시명·**지시/응답 SQLite 기록·관리자 집계 UI**·**관리자 UI 6개국어 + `locale-config` 기반 국기 순서**·근로자용 i18n 번역/easy-ja API·**커리큘럼 시트 탭 UI + 지점 전용 용어/표현 DB**.
2. **Phase 2 (LMS):** 개호 분야 학습 콘텐츠 연동 및 기초 게임화.
3. **Phase 3 (고도화):** B2B 리포트 기능, giftee API 연동, PWA 최적화.

---

## 6. 초기 개발 실행 가이드 (Cursor AI용)

1. **환경 설정:** 가상환경(`venv`) 활성화 및 필수 라이브러리 설치.
2. **서버 구축:** `main.py`에 FastAPI 및 WebSocket 로직 구현.
3. **데이터 연동:** `kaigo_project` 폴더 내 엑셀/시트 데이터를 읽어 JSON API로 제공.

### 6.1 스마트폰으로 참가용 QR 스캔 시

- QR·참가 URL은 `PUBLIC_BASE_URL`(기본 `http://127.0.0.1:8000`)을 사용합니다. **휴대폰은 `127.0.0.1`로 PC 서버에 접속할 수 없습니다** (그 주소는 폰 자신을 가리킵니다).
- 같은 Wi‑Fi에서 테스트할 때: PC의 LAN IP를 확인한 뒤 `.env`에 `PUBLIC_BASE_URL=http://<LAN IP>:<포트>` 로 설정하고 서버를 재시작한 뒤 **관리자 화면에서 QR을 다시 열어** 새 코드를 사용합니다.
- PC가 외부에서 접속을 받으려면 Uvicorn을 `0.0.0.0`에 바인딩해야 합니다. 예: `uvicorn main:app --host 0.0.0.0 --port 8000`
- 참가 토큰은 **1회용**입니다. 이미 한 번 열면 같은 QR로는 다시 들어가지 못할 수 있으니, 필요하면 QR을 새로 발급합니다.

### 6.2 워크스페이스 저장 (SQLite, 무료·로컬)

- 워크스페이스(이름·회사·지점·부서 등)는 **Python 표준 `sqlite3`** 로 `data/workbridge.db` 파일에 저장됩니다. 별도 pip 패키지는 필요 없습니다.
- 경로는 `.env`의 `DATABASE_URL`로 바꿀 수 있습니다 (기본 `sqlite:///./data/workbridge.db`). `data/`는 `.gitignore`에 포함되어 있습니다.
- **영구 테이블(예시):** `staff_groups`, `workspace_staff_accounts`(컬럼 `group_id` 등), 지시 기록 `instruction_rounds`(선택 **`image_url`**) / 수신자 `instruction_recipients` / 응답 `instruction_replies`, 지점 전용 용어 `workspace_glossary_terms`·`workspace_expression_terms` 등. 마이그레이션은 `app/db/sqlite.py`의 `get_connection()` 시 `CREATE TABLE IF NOT EXISTS` 및 `ALTER TABLE`로 처리합니다. 관리자·스태프 아바타·**지시용 이미지**는 `static/uploads/` 하위(일부는 `.gitignore`로 업로드 실제 파일 제외, `.gitkeep`만 유지).
- 로그인 세션·참가 토큰은 여전히 **메모리**에만 있어 서버 재시작 시 끊깁니다.

### 6.3 브라우저에서 쓰는 로그인·대표 URL (로컬 PC, 포트 8000 예시)

아래는 **`http://` + 호스트 + 포트 + 경로**까지 **한 줄**으로 주소창에 넣는 주소입니다. **`/worker` 페이지를 연 채로 주소만 `login`으로 고치면** `/worker/login` 이 되어 404가 나므로, **항상 아래 전체 URL**을 쓰거나 화면의 **「ログイン画面へ」** 버튼을 누릅니다.

| 구분 | 주소 (예: 같은 PC에서) |
|------|------------------------|
| **통합 로그인 (역할 선택)** | `http://127.0.0.1:8000/login` |
| **관리자 폼 바로** | `http://127.0.0.1:8000/login?role=admin` |
| **현장 스태프 폼 바로** | `http://127.0.0.1:8000/login?role=worker` |
| **総運営 폼 바로** | `http://127.0.0.1:8000/login?role=super_admin` |
| **루트 (로그인으로 리다이렉트)** | `http://127.0.0.1:8000/` |
| **입구 (CDN 없음·로그인 링크만)** | `http://127.0.0.1:8000/enter` |
| **스태프 앱 (토큰·세션 없으면 게이트만)** | `http://127.0.0.1:8000/worker` |
| **관리자 앱 (세션 없으면 게이트·안내)** | `http://127.0.0.1:8000/admin` |
| **서버 생존 확인 (JSON)** | `http://127.0.0.1:8000/health` → **`service`: `workbridge-fastapi`** 포함. 이만 빈 `{"detail":"Not Found"}` 이면 **8000 포트가 다른 프로그램**일 수 있음 |

**같은 Wi‑Fi의 스마트폰 등**에서는 `127.0.0.1` 대신 **`http://<PC의 LAN IP>:8000/...`** 를 씁니다. 휴대폰에서 `127.0.0.1`은 **폰 자신**을 가리키므로 PC 서버에 연결되지 않습니다.