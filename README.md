# 텔레그램 성구 암송 봇

텔레그램 버튼으로 성구 10개를 보여 주고, 선택한 성구를 `전체 암기` 또는 `빈칸 넣기` 방식으로 연습하는 챗봇입니다.

## 기능

- 성구 10개 버튼 목록
- 선택한 성구 원문 확인
- 전체 암기: 사용자가 입력한 문장을 원문과 비교해 점수와 피드백 제공
- 빈칸 넣기: `하`, `중`, `상`, `최상` 난이도에 따라 빈칸 수 조절
- 빈칸 넣기 보기 버튼: 사용자가 정답 후보를 빈칸 순서대로 눌러 자동 채점
- 최상 난이도: 빈칸 답을 하나씩 직접 입력하는 주관식 빈칸
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

```bash
copy .env.example .env
```

`.env`:

```env
TELEGRAM_BOT_TOKEN=발급받은_토큰
ADMIN_USER_IDS=관리자_텔레그램_ID
QUIZ_STATE_TTL_DAYS=3
ERROR_LOG_TTL_DAYS=14
TRANSIENT_NETWORK_ERROR_LOG_INTERVAL_SECONDS=600
```

## 실행

```bash
python bot.py
```

텔레그램에서 만든 봇에게 `/start` 를 보내면 성구 선택 버튼이 표시됩니다.

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
ADMIN_USER_IDS=관리자_텔레그램_ID
QUIZ_STATE_TTL_DAYS=3
ERROR_LOG_TTL_DAYS=14
TRANSIENT_NETWORK_ERROR_LOG_INTERVAL_SECONDS=600
```

데이터 저장 폴더를 만들고 실행합니다.

```bash
mkdir data
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
```

관리자 명령어는 `.env`의 `ADMIN_USER_IDS`에 등록된 텔레그램 사용자 ID만 사용할 수 있습니다. 여러 명을 등록하려면 쉼표로 구분합니다.

운영 데이터 보관 기간:

- `QUIZ_STATE_TTL_DAYS`: 진행 중인 퀴즈 상태 보관 일수, 기본 3일
- `ERROR_LOG_TTL_DAYS`: 에러 로그 보관 일수, 기본 14일
- `TRANSIENT_NETWORK_ERROR_LOG_INTERVAL_SECONDS`: 일시적 네트워크 에러 기록 최소 간격, 기본 600초

## 매일 리마인더

봇 채팅방에서 아래 명령을 입력하면 매일 오전 8시마다 10개 성구 중 랜덤으로 1개가 전송됩니다.

```text
/remind_on
```

성구 선택 화면의 `🔔 매일 8시 리마인더 받기` 버튼으로도 켤 수 있습니다.

리마인더는 봇 프로그램이 켜져 있어야 전송됩니다. 컴퓨터를 끄면 알림도 멈추므로, 계속 사용하려면 봇을 서버나 항상 켜져 있는 PC에서 실행하세요.

## 성구 수정

성구 목록은 `scriptures.py` 에 있습니다. 현재 항목은 요청한 요한계시록 10개 구절로 맞춰져 있습니다.

개역한글 본문은 저작권이 있는 번역본이므로, 사용 권한이 있는 본문을 `text` 값에 직접 붙여 넣어 사용하세요.
