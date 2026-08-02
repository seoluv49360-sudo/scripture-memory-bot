# 텔레그램 성구 암송 봇

텔레그램 버튼으로 성구 5개를 보여 주고, 선택한 성구를 `전체 암기` 또는 `빈칸 넣기` 방식으로 연습하는 챗봇입니다.

## 기능

- 성구 5개 버튼 목록
- 선택한 성구 원문 확인
- 전체 암기: 사용자가 입력한 문장을 원문과 비교해 점수와 피드백 제공
- 빈칸 넣기: `하`, `중`, `상`, `최상` 난이도에 따라 빈칸 수 조절
- 빈칸 넣기 보기 버튼: 사용자가 정답 후보를 빈칸 순서대로 눌러 자동 채점
- 최상 난이도: 빈칸 답을 하나씩 직접 입력하는 주관식 빈칸
- 3문제/5문제 전체암기 모의고사
- 매일 오전 8시 랜덤 성구 리마인더
- `/cancel` 로 현재 문제 취소

## 설치

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 텔레그램 토큰 설정

1. 텔레그램에서 `@BotFather` 를 열고 `/newbot` 으로 봇을 만듭니다.
2. 발급받은 토큰을 `.env` 파일에 넣습니다.

과거에 토큰이 Git, 채팅, 화면 캡처 등에 노출된 적이 있다면 BotFather의
`/revoke`로 기존 토큰을 먼저 폐기하고 새 토큰을 발급받아야 합니다. 새 토큰을
적용한 뒤에만 `BOT_TOKEN_ROTATION_CONFIRMED=true`로 설정하세요. 봇은 이 확인값과
토큰 형식을 모두 검사하고, 조건이 맞지 않으면 시작하지 않습니다.

```bash
copy .env.example .env
```

`.env`:

```env
TELEGRAM_BOT_TOKEN=발급받은_토큰
BOT_TOKEN_ROTATION_CONFIRMED=true
MEMBER_ACCESS_TOKEN=
ADMIN_USER_IDS=관리자_텔레그램_ID
SCRIPTURES_FILE=data/scriptures.json
REQUEST_LIMIT_COUNT=12
REQUEST_LIMIT_WINDOW_SECONDS=10
AUTH_FAILURE_LIMIT=5
AUTH_FAILURE_WINDOW_SECONDS=900
AUTH_BLOCK_SECONDS=1800
QUIZ_STATE_TTL_DAYS=3
ERROR_LOG_TTL_DAYS=14
TRANSIENT_NETWORK_ERROR_LOG_INTERVAL_SECONDS=3600
```

## 실행

```bash
python bot.py
```

`MEMBER_ACCESS_TOKEN`은 선택 설정입니다. 값을 비워 두면 별도 승인 없이 개인 채팅에서 `/start`로 바로 이용할 수 있습니다.
초대 링크로 사용자 승인을 제한하고 싶을 때만 32~64자의 무작위 토큰을 넣고, 아래 형식의 deep-link 초대 링크를 전달합니다.

```text
https://t.me/내봇사용자이름?start=MEMBER_ACCESS_TOKEN값
```

토큰을 설정한 경우 사용자가 링크를 처음 누르면 승인 멤버로 등록됩니다. 이후에는 일반 `/start` 명령으로도 이용할 수 있습니다. `.env`의 `ADMIN_USER_IDS`에 등록된 관리자는 초대 링크 없이 접속할 수 있습니다.

## Docker로 실행

미니PC에서 Docker를 사용한다면 아래 방식이 가장 간단합니다.

```bash
git clone https://github.com/seoluv49360-sudo/scripture-memory-bot.git
cd scripture-memory-bot
cp .env.example .env
```

`.env` 파일에 BotFather 토큰을 넣습니다.

```env
TELEGRAM_BOT_TOKEN=발급받은_토큰
BOT_TOKEN_ROTATION_CONFIRMED=true
MEMBER_ACCESS_TOKEN=
ADMIN_USER_IDS=관리자_텔레그램_ID
SCRIPTURES_FILE=data/scriptures.json
REQUEST_LIMIT_COUNT=12
REQUEST_LIMIT_WINDOW_SECONDS=10
AUTH_FAILURE_LIMIT=5
AUTH_FAILURE_WINDOW_SECONDS=900
AUTH_BLOCK_SECONDS=1800
QUIZ_STATE_TTL_DAYS=3
ERROR_LOG_TTL_DAYS=14
TRANSIENT_NETWORK_ERROR_LOG_INTERVAL_SECONDS=3600
```

데이터 저장 폴더를 만들고 예제 본문 파일을 복사한 뒤, 사용 권한이 있는 실제
본문과 라벨을 `data/scriptures.json`에 입력합니다.

```bash
mkdir data
cp scriptures.example.json data/scriptures.json
docker compose up -d --build
```

로그 확인:

```bash
docker compose logs -f
```

중지:

```bash
docker compose down
```

`restart: unless-stopped` 설정이 있어 미니PC가 재부팅되어도 Docker가 켜져 있으면 봇이 자동으로 다시 시작됩니다.

운영 상태 확인:

```text
/status
```

오늘/어제/최근 7일/누적 방문자 수, 오늘 신규 방문자 수, 리마인더 등록 수, 진행 중인 퀴즈 상태 수, 기록된 에러 수를 확인할 수 있습니다. 방문자는 하루 기준 사용자 ID 중복을 제외해 계산합니다. 리마인더와 진행 중인 퀴즈 상태는 `data/bot.sqlite3`에 저장됩니다.

관리자 명령어:

```text
/admin_status
/admin_errors
/admin_reset_errors
/admin_add 텔레그램사용자ID
/admin_remove 텔레그램사용자ID
```

관리자 명령어는 관리자만 사용할 수 있습니다. `.env`의 `ADMIN_USER_IDS`는 서버
복구용 비상 관리자이며 실행 시 DB 관리자 목록에도 반영됩니다. 여러 명을
등록하려면 쉼표로 구분합니다. 비상 관리자, 현재 명령을 실행 중인 관리자,
마지막 남은 관리자는 제거할 수 없어 관리자 lockout을 방지합니다.

봇은 개인 채팅에서만 동작하며 그룹이나 슈퍼그룹에 추가되면 해당 채팅을
처리하지 않고 나갑니다. 사용자별 짧은 시간의 요청 횟수와 잘못된 초대 토큰
시도 횟수도 제한됩니다.

`MEMBER_ACCESS_TOKEN`은 선택값입니다. 비워 두면 초대 승인 기능이 꺼지고, 값을 넣으면 승인된 사용자만 이용할 수 있습니다. 사용할 경우 비밀번호 생성기로 만든 긴 무작위 문자열을 사용하세요. 실제 값은 `.env`에만 넣고 Git이나 채팅에 공개하지 마세요. 토큰을 변경해도 이미 승인된 사용자는 계속 이용할 수 있으며, 새 사용자는 새 초대 링크를 사용해야 합니다.

PowerShell에서는 다음 명령으로 안전한 형식의 토큰을 만들 수 있습니다.

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

모의고사:

```text
/mock
/mock5
```

`/mock`은 5개 성구 중 랜덤으로 3개, `/mock5`는 5개 성구 전체가 출제됩니다. 전체 암기 방식으로 입력하며, 점수는 마지막에 한 번에 공개되고 결과 화면에서 전체 틀린 부분과 복습 추천을 확인할 수 있습니다.

운영 데이터 보관 기간:

- `QUIZ_STATE_TTL_DAYS`: 진행 중인 퀴즈 상태 보관 일수, 기본 3일
- `ERROR_LOG_TTL_DAYS`: 에러 로그 보관 일수, 기본 14일
- `TRANSIENT_NETWORK_ERROR_LOG_INTERVAL_SECONDS`: 일시적 네트워크 에러 기록 최소 간격, 기본 3600초

## 매일 리마인더

봇 채팅방에서 아래 명령을 입력하면 매일 오전 8시마다 5개 성구 중 랜덤으로 1개가 전송됩니다.

```text
/remind_on
```

성구 선택 화면의 `🔔 매일 8시 리마인더 받기` 버튼으로도 켤 수 있습니다. 구독 중에는 `🔕 매일 8시 리마인더 해제` 버튼으로 끌 수 있으며, 매일 8시에 도착하는 리마인더 메시지에도 해제 버튼이 함께 표시됩니다.

리마인더는 봇 프로그램이 켜져 있어야 전송됩니다. 컴퓨터를 끄면 알림도 멈추므로, 계속 사용하려면 봇을 서버나 항상 켜져 있는 PC에서 실행하세요.

## 성구 수정

실제 본문과 표시 라벨은 Git에서 제외되는 `data/scriptures.json`에 저장합니다.
저장소에는 구조만 보여 주는 `scriptures.example.json`만 포함됩니다.

PowerShell에서는 다음 명령으로 운영 파일을 만들 수 있습니다.

```powershell
New-Item -ItemType Directory -Force data
Copy-Item scriptures.example.json data\scriptures.json
notepad data\scriptures.json
```

개역한글 본문처럼 공개하기 곤란하거나 사용 조건이 있는 데이터는
`data/scriptures.json`에만 입력하고 커밋하지 마세요. 과거 Git 커밋에 실제 토큰이나
본문이 포함된 적이 있다면 현재 파일 삭제만으로 기록이 사라지지 않습니다. 토큰은
반드시 폐기·재발급하고, 저장소 공개 전에는 별도로 Git 기록 정리 여부를 검토해야
합니다.

개역한글 본문은 저작권이 있는 번역본이므로, 사용 권한이 있는 본문을 `text` 값에 직접 붙여 넣어 사용하세요.

## 라이브 데모 API (`demo_api/`)

services-portal(개발 현황 브리핑 포털)에서 실제로 이 봇과 대화해볼 수 있게 해주는 별도 API입니다.
`bot.py`를 그대로 import해서 진짜 핸들러 코드를 실행하되, 텔레그램 네트워크는 전혀 쓰지 않습니다
(`demo_api/fake_telegram.py`가 전송 메서드를 가로채서 응답을 기록만 함). **실제 운영 봇(위 `docker
compose up`으로 띄운 것, 진짜 텔레그램 폴링)과는 완전히 분리된 별도 컨테이너·별도 SQLite 파일**을
쓰므로 운영 데이터에는 전혀 영향이 없습니다.

### 로컬에서 확인

```bash
pip install -r demo_api/requirements.txt
uvicorn demo_api.main:app --reload
```

```bash
curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" -d "{\"session_id\":\"test\"}"
```

### 배포

1. `.env`에 `DEMO_SITE_DOMAIN`/`DEMO_BASIC_AUTH_USER`/`DEMO_BASIC_AUTH_HASH`/`DEMO_ALLOWED_ORIGINS` 채우기
   (해시 생성: `docker run --rm caddy:2-alpine caddy hash-password --plaintext '비밀번호'`,
   `.env`에 넣을 때 해시의 `$`는 `$$`로 두 번씩 — docker compose 변수치환 함정).
2. 기존 봇과 별개로 데모 API만 기동:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.demo.yml up -d --build
   ```
3. 이 PC/서버에 80/443 쓰는 다른 Caddy가 없으면 `docker-compose.standalone-caddy.yml`도 같이 붙이고,
   있으면 services-portal에서 했던 것과 같은 방식으로 그 Caddy에 이 프로젝트의 `Caddyfile` 내용
   (`basic_auth` 포함)을 추가하고 네트워크 연결 후 reload.
4. services-portal `src/data/services.ts`의 `scripture-memory-bot` 항목에 `liveBotDemo.apiUrl`을
   이 데모 API의 실제 URL로 채우기.

### 알아둘 점

- 세션 상태는 프로세스 메모리에만 있음 — 컨테이너 재시작하면 전부 초기화됨(의도된 동작).
- `RecordingBot`은 실제 `TELEGRAM_BOT_TOKEN`이 필요 없음(네트워크 호출 자체를 안 함).
- 리마인더 예약(`🔔 매일 8시 리마인더 받기`)이나 관리자 명령 등 일부 분기는 데모에서 의도적으로
  지원 범위 밖입니다 — 핵심 퀴즈 흐름(성구 선택 → 전체암기/빈칸넣기 → 채점)만 검증됨.
