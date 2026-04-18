# WorkBridge Japan — 프로젝트 정의서 (v1.4)

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
3. **지시 전송 대상:** 기본은 워크스페이스 내 **전원 브로드캐스트**. 관리자 화면에서 **선택한 접속 근로자에게만** 동일 지시를 전달하는 모드 제공(온라인 근로자 목록·체크 선택, WebSocket `instruction`에 `target_tokens` 지정).
4. **근로자 식별 표시:** 근로자 화면 상단에 **「スタッフ」+ 번호 또는 이름**을 표시. 조인 시 `user_label`이 없으면 워크스페이스별 자동 번호(`No.1`, `No.2` …), 있으면 해당 문자열(이름)을 표시·세션에 저장. (선택) `/worker?join_token=…&user_label=…` 또는 `GET /api/v1/auth/join?token=…&user_label=…` 로 이름 지정. 이후 이름 변경은 DB(향후) 또는 별도 API로 확장 가능.
5. **포털 로그인 (MVP):** `/login` 에서 **管理者 / 現場スタッフ** 선택 후 **ログインID・パスワード**. `POST /api/v1/auth/portal-login` — 관리자는 **ワークスペース名** + `portal_admin_password`(기본 `admin`, `.env`로 변경), 스태프는 **ワークスペースID 또는同名** + `portal_worker_password`(기본 `worker`). 성공 시 `sessionStorage`에 토큰 저장 후 `/admin`·`/worker`로 이동. **メニュー**からログアウトで `/login`へ。管理者メニュー: **QRコード**（参加用）、**用語・単語登録**（説明モーダル）、**ユーザー一覧**（`online-workers`）。
6. **総運営スーパー管理者（運営会社）:** `portal-login` 의 `role: super_admin` + `super_admin_password`(기본 `superadmin`, `.env`의 `SUPER_ADMIN_PASSWORD`). `GET /api/v1/workspaces?super_token=…` 로 전체 워크스페이스 목록, `POST /api/v1/auth/super-assume` 로 선택 워크스페이스의 **관리자 토큰 발급** 후 `/admin` 과 동일 UI. WebSocket 은 슈퍼 토큰으로 연결 불가(반드시 assume 후 관리자 토큰 사용). `/super` 페이지에서 목록·선택.
7. **管理者 UI レイアウト:** メニューで **PCレイアウト**（広い2カラム・用語/追加管理者の説明パネル）と **スマホレイアウト**（従来の狭幅）を切替。`localStorage` 키 `wb_admin_layout`.
8. **현장별 전용 사전:** 3계층 사전(Global / Industry / Site-specific) 적용 및 구글 스프레드시트 연동.

### 3.2 학습 및 게임화
1. **스마트 플래시카드:** TTS 발음 재생 지원 및 업종별 커리큘럼(1차: 개호, 2차: 음식업).
2. **포인트 리워드:** 학습 성취도에 따라 일본 편의점 쿠폰(giftee API) 교환.

---

## 4. 기술 스택 (Tech Stack)

- **Backend:** Python (FastAPI), WebSockets
- **Frontend:** PWA (HTML/JS/Tailwind CSS)
- **Database:** PostgreSQL (SQLAlchemy ORM), Google Sheets API
- **AI/ML:** OpenAI Whisper (STT), DeepL/Google Translate (번역), Google TTS
- **Environment:** Windows 10 x64, Python 3.9 (32bit)

---

## 5. 개발 로드맵

1. **Phase 1 (MVP):** WebSocket·QR/조인·포털·슈퍼관리자(목록·`super-assume`)·`GET /auth/session`·관리자 PC/모바일 레이아웃·메뉴(QR·用語·ユーザー)·전원/개별 지시·`online-workers`·근로자 표시명.
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
- 로그인 세션·참가 토큰은 여전히 **메모리**에만 있어 서버 재시작 시 끊깁니다.