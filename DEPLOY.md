# Render 배포 가이드

이 폴더의 `Dockerfile` + `render.yaml`만 있으면 Render가 자동으로 빌드·실행합니다. 같은 Dockerfile은 나중에 Fly.io(도쿄), Cloud Run, AWS App Runner로 그대로 옮길 수 있습니다.

## 1. GitHub에 코드 올리기

Render는 GitHub repo에서 자동 배포합니다. 이 worktree의 `claude/laughing-visvesvaraya-38c12d` 브랜치를 main에 머지하거나, 별도 브랜치를 GitHub에 push하세요.

```bash
# main 으로 머지하는 경우
git checkout main
git merge claude/laughing-visvesvaraya-38c12d
git push origin main
```

## 2. Render에서 Blueprint 생성

1. Render 대시보드 → **New** → **Blueprint**
2. GitHub repo 선택 → 브랜치 선택 (예: `main`)
3. Render가 자동으로 `render.yaml`을 읽어 한 개의 web service를 만듭니다.

## 3. 환경변수 설정 (Apply 누르기 전에)

Blueprint 화면에서 `sync: false` 로 비워둔 항목을 채워 넣습니다.

| 키 | 값 | 비고 |
|---|---|---|
| `PORTAL_ADMIN_PASSWORD` | 원하는 강한 비번 | 관리자 로그인 비번 |
| `SUPER_ADMIN_PASSWORD` | 원하는 강한 비번 | 총운영 로그인 비번 |
| `SESSION_SECRET` | 랜덤 48바이트 문자열 | JWT 서명 키. `python -c "import secrets;print(secrets.token_urlsafe(48))"` 으로 생성. 바꾸면 모든 사용자 재로그인 |
| `PUBLIC_BASE_URL` | (일단 비워두고 첫 deploy 후 채우기) | Render가 발급하는 `https://workbridge-xxxx.onrender.com` |

`PUBLIC_BASE_URL`은 Render가 도메인을 발급한 다음에야 알 수 있으므로, 일단 빈값으로 deploy → 발급된 URL을 복사해서 환경변수에 넣고 → 한 번 더 재시작하면 됩니다.

## 4. Google 인증 키 (선택)

번역(`/api/v1/i18n/translate`)·やさしい日本語 API를 쓰려면 GCP 서비스 계정 JSON이 필요합니다. **로컬의 `google_key.json`을 절대 git에 올리지 마세요.**

대신 Render의 **Secret Files** 기능을 씁니다:
1. 서비스 화면 → **Environment** → **Secret Files** → **Add Secret File**
2. **Filename**: `google_key.json`
3. **Contents**: 로컬 `google_key.json` 파일 내용 그대로 붙여넣기

`render.yaml`에 이미 `GOOGLE_CREDENTIALS_PATH=/etc/secrets/google_key.json`이 잡혀있어 자동으로 인식됩니다.

번역 API를 안 쓸 거면 Secret File 없이 진행해도 동작합니다(번역 호출 시 503으로만 응답).

## 5. 첫 배포 → URL 받기 → `PUBLIC_BASE_URL` 설정

1. Apply 누르고 5~10분 기다리면 빌드·배포 완료
2. 받은 URL (예: `https://workbridge-xxxx.onrender.com`) 복사
3. **Environment** → `PUBLIC_BASE_URL` 채우기 → **Save Changes** (자동 재시작)
4. `https://<url>/login` 으로 접속

## 6. 데이터 영속성 — 플랜 선택

| 플랜 | 월 요금 | 디스크 | 슬립 | 적합 용도 |
|---|---|---|---|---|
| **Starter** *(현재 `render.yaml` 기본값)* | $7 + $0.25/GB | **1GB 영속 디스크** (워크스페이스/DB/업로드 보존) | 없음 | 시연·사용자 테스트 |
| **Free** | $0 | 디스크 없음 — 매 배포·휴면 후 데이터 초기화 | 15분 무활동 후 자동 슬립 (다음 요청 30초 대기) | 1회성 점검만 |

Free로 가려면 `render.yaml`에서 `plan: starter`를 `plan: free`로 바꾸고 `disk:` 블록 4줄을 주석/삭제하세요. 단 sibuya 워크스페이스·스태프 계정·지시 이력 모두 매 배포마다 사라집니다.

## 7. 첫 로그인 — 부트스트랩

Render에서 처음 띄우면 DB가 비어있습니다. 워크스페이스 만드는 순서:

1. `https://<url>/login` → **管理者**
2. ワークスペース名: `sibuya` (자동 생성됨)
3. パスワード: 위에서 정한 `PORTAL_ADMIN_PASSWORD`
4. 관리자 화면 메뉴 → **「スタッフ登録」** → 스태프 ID·표시명·비번 입력해서 계정 발급
5. 발급한 ID로 다른 기기/탭에서 `/login?role=worker` 로그인

## 8. 외부망/스마트폰 테스트

Render는 자동으로 HTTPS를 발급하므로, `https://<url>` 그대로 스마트폰에서 접속하면 됩니다. **Wi-Fi/LAN/127.0.0.1 신경 쓸 필요 없음**(공개 인터넷이라).

## 트러블슈팅

| 증상 | 원인/대처 |
|---|---|
| 빌드 실패: `pip install` 단계 | requirements.txt의 패키지 버전이 OS와 안 맞을 때. Render 대시보드 → **Logs** 확인 |
| 502/503 직후 배포 | uvicorn이 `$PORT`에 바인딩 못 함. 보통 첫 부팅 시 1분 정도 기다리면 통과 |
| WebSocket 연결 안 됨 | Render는 자동으로 WS를 지원함. 브라우저 개발자도구 → Network → `ws://`가 아닌 `wss://`로 연결되는지 확인 |
| 로그인은 되는데 새로고침하면 풀림 | `sessionStorage`라 탭 닫으면 사라짐(원래 동작). 영속 로그인이 필요하면 차후 작업 |
| Google 번역 503 | `google_key.json` Secret File이 없거나 GCP에서 Translation API가 비활성화됨 |

## 일본 리전으로 옮길 때

Render는 도쿄 리전이 없습니다. 옮길 때 후보:

| 호스트 | 리전 | 같은 Dockerfile 그대로? |
|---|---|---|
| **Fly.io** | `nrt` (도쿄) | ✅ `fly launch`로 그대로 |
| **GCP Cloud Run** | `asia-northeast1` (도쿄) | ✅ `gcloud run deploy --source .` |
| **AWS App Runner** | 도쿄 | ✅ ECR push 후 service 생성 |

이 가이드의 Render 환경변수·Secret File 매핑만 해당 호스트의 형식으로 옮기면 됩니다. 코드 변경은 필요 없습니다.
