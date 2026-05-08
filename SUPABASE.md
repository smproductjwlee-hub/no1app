# Postgres 이전 — Supabase 설정 가이드

이 앱은 `DATABASE_URL` 환경변수만 바꾸면 SQLite ↔ Postgres 전환됩니다. 코드 변경 없음.

- `sqlite:///./data/workbridge.db` → SQLite (로컬 개발)
- `postgresql://...` → Postgres (운영)

---

## 1. Supabase 프로젝트 만들기 (5분)

1. https://supabase.com 가입 / 로그인
2. **「New project」** 클릭
3. 입력:
   - **Name**: `workbridge` (자유)
   - **Database Password**: 강한 비번 (Supabase 콘솔에서만 쓰임 — 별도로 보관)
   - **Region**: 일본 사용자라면 **`Northeast Asia (Tokyo)`** ⭐ (지연 ~5ms)
   - **Plan**: Free (500MB DB, 50K MAU) — 시연·초기 운영 충분. Pro $25/mo는 8GB
4. 「Create new project」 → 1~2분 대기

## 2. Connection String 복사

프로젝트 대시보드 → 좌측 톱니 **「Project Settings」** → **「Database」** → **「Connection string」** 섹션

3가지 옵션이 있습니다. **반드시 「Transaction pooler」 (`...pooler.supabase.com:6543...`)** 를 선택하세요:

| 종류 | 포트 | 용도 |
|---|---|---|
| Direct connection | 5432 | DB 마이그레이션 도구용. 60 connection 제한 |
| Session pooler | 5432 | 같은 connection 안 같은 트랜잭션 |
| **Transaction pooler** ⭐ | **6543** | **우리 앱 권장**. PgBouncer가 connection 다중화 |

복사한 URL은 다음 형태:
```
postgresql://postgres.[프로젝트ref]:[YOUR-PASSWORD]@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres
```

`[YOUR-PASSWORD]` 부분에 1단계의 Database Password를 넣으세요.

## 3. Render에 환경변수 등록

1. Render 대시보드 → `workbridge` 서비스 → **「Environment」** 탭
2. **「Add Environment Variable」**:
   - Key: `DATABASE_URL`
   - Value: 위에서 복사한 Postgres URL (비번 채운 것)
3. **「Save Changes」** — 자동 재배포 (3~5분)

## 4. 검증

배포 끝나면 (Events에 「Live」):

1. `https://workbridge-6f1d.onrender.com/health` → `{"status":"ok",...}` ✓
2. `https://workbridge-6f1d.onrender.com/login` → 로그인 페이지 정상 ✓
3. **로그 확인**: Render → Logs 에 다음 같은 게 있으면 정상:
   ```
   Application startup complete.
   ```
4. 새 워크스페이스 생성 + 스태프 계정 등록 + 워커 로그인 + 지시 송수신 시도

## 5. Supabase 쪽에서 데이터 확인

Supabase 대시보드 → **「Table Editor」** 에서 다음 테이블이 보여야 정상:
- `workspaces`
- `workspace_staff_accounts`
- `staff_groups`
- `instruction_rounds` / `instruction_recipients` / `instruction_replies`
- `ws_presence`
- `workspace_chat_messages`
- `workspace_glossary_terms` / `workspace_expression_terms`
- `worker_glossary_saves`
- `translation_cache` / `easy_ja_cache`

---

## 트러블슈팅

| 증상 | 원인/대처 |
|---|---|
| 500 + 로그에 `psycopg.OperationalError: connection refused` | DATABASE_URL 비밀번호 오타. 다시 복사해 붙여넣기 |
| 500 + `too many connections` | Direct connection(5432)을 쓰고 있을 가능성. **Transaction pooler(6543)** 로 바꿔야 함 |
| 500 + `relation does not exist` | 첫 부팅 때 init_db 실패. Render 로그 확인 후 Manual Deploy 다시 |
| 로그인 가능하지만 스태프 등록 시 409 | 같은 login_id 중복 — 정상 동작 (UNIQUE 제약) |
| Render 배포 끝났는데 `/health` 가 502 | 첫 부팅 시 Supabase 연결 + 스키마 작성에 30~60초 걸림. 1분 더 기다리기 |

## 풀 사이즈 조정 (선택)

기본값으로 16개의 PG connection을 사용합니다. Render Pro로 업그레이드 + 동시 사용자가 많아지면:

```
DB_POOL_MAX_SIZE=32
```

환경변수 추가. Supabase Free의 Transaction Pooler는 200 connection까지 받으므로 여러 인스턴스를 띄워도 여유가 있습니다.

## 데이터 마이그레이션 (선택)

현재 Render Free SQLite는 매 배포마다 초기화되므로, **데이터 마이그레이션 불필요** — Postgres로 바로 시작하면 됩니다.

만약 보존할 SQLite 데이터가 있으면:
1. SQLite 파일을 로컬로 다운로드
2. `pgloader` 도구 사용: `pgloader sqlite:///path/to/db.sqlite postgresql://...`
3. 또는 `app/db/sqlite.py`의 SQL을 손으로 INSERT 변환
