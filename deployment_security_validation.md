# 텔레그램 봇 배포 전 보안 검증

검증 기준: `붙임3 텔레그램봇배포전보안검증.md` v260527 / 개정 260606

## 결과 표

| 항목ID | 판정 | 근거 |
|---|---|---|
| C1 | 적합 | `load_dotenv(override=False)`, `os.getenv("TELEGRAM_BOT_TOKEN")`, `os.getenv("MEMBER_ACCESS_TOKEN")`로 비밀값을 환경변수에서 읽는다. 코드에 실제 비밀값이 없다. |
| C2 | 적합 | `.gitignore`에 `.env`가 있고 `.env.example`은 자리표시자만 포함한다. `.env` 추적 이력은 없다. 과거 `.env.example` 토큰 노출 정황은 있었으나 사용자가 2026-06-13 BotFather 재발급을 완료했다. |
| C3 | 적합 | `member_access_payload_matches()`가 `hmac.compare_digest()`를 사용하며, 승인되지 않은 사용자는 정확한 deep-link payload 없이는 등록되지 않는다. |
| C4 | 적합 | `require_member()`와 `require_admin()`이 분리되어 있다. 관리자 추가·제거는 관리자 명령만 가능하고, 자기 자신·비상 관리자·마지막 관리자는 제거할 수 없다. 첫 입장자 자동 관리자 로직은 없다. |
| C5 | 적합 | SQL 값은 `?` 매개변수를 사용한다. 성구 ID는 `SCRIPTURE_BY_ID`로 검증하고 빈칸 번호는 숫자·범위·현재 메시지를 검사한다. 사용자 HTML 출력은 `html.escape()` 처리한다. |
| C6 | 적합 | `safe_update_summary()`는 update ID와 종류만 저장하고 `safe_error_summary()`는 예외 클래스만 저장한다. 사용자에게는 간단한 재시작 안내만 전송한다. |
| C7 | 적합 | `eval`, `exec`, `os.system`, 사용자 입력과 결합된 셸 실행 코드가 없다. |
| C8 | 적합 | `request_allowed()`가 사용자별 요청을 제한하고 `record_auth_failure()`가 잘못된 초대 토큰 반복 시도를 DB에 기록해 일시 차단한다. |
| C9 | 적합 | `require_private_chat()`이 개인 채팅만 허용하며 그룹·슈퍼그룹에서는 `leave_chat()`을 호출한다. 비공개 채팅이 아닌 메시지는 별도 핸들러에서도 차단한다. |
| C10 | 부적합 | `bot.py`, `scriptures.py`, `README.md`에 `성구`, `암송`, `요한계시록`, `scripture` 등 종교·내부 용어가 남아 있다. 실제 본문과 참조 라벨은 `data/scriptures.json`으로 외부화되어 Git에서 제외됐지만, 코드와 메시지 전체 중립화 기준은 충족하지 못한다. |
| C11 | 적합 | Python 스택이며 `requirements.txt`에 버전이 고정되어 있다. `python-dotenv==1.2.2`로 갱신했고 `pip-audit -r requirements.txt` 결과 알려진 취약점이 없다. |
| C12 | 적합 | `app.run_polling()`만 사용하며 webhook 또는 공개 HTTP 엔드포인트 코드가 없다. |

## 종합 판정

**조건부 가능(최소 수정 필요)**

치명적 항목 C1·C3·C5·C6·C7은 모두 적합이다. 과거 노출 토큰은
BotFather에서 재발급됐고 C11 도구 교차 확인도 완료됐다. 남은 부적합은 배포
차단 항목이 아닌 C10이다.

✅ 검증 자가 점검 완료 (C1~C12 전수 판정 확인)

## 부적합 항목별 수정 제안

### C10

현재 실제 본문과 참조 라벨은 이미 `data/scriptures.json`으로 외부화되어 있다.
문서 기준을 문자 그대로 충족하려면 버튼·안내문도 별도의
`data/ui_labels.json`으로 옮기고, 추적 코드의 `scripture` 계열 변수·파일명도
`content_item` 같은 중립적 이름으로 변경해야 한다.

예시 설정:

```json
{
  "select_item": "암송할 성구를 선택하세요.",
  "full_practice": "전체 암기",
  "blank_practice": "빈칸 넣기",
  "daily_reminder": "오늘의 암송 리마인더"
}
```

`.gitignore`에는 이미 `data/`가 포함되어 있으므로 이 파일은 Git에 올라가지 않는다.
다만 이 봇은 종교 용어가 사용자 기능의 핵심이므로, C10을 완전히 중립화하면
공개 저장소만으로는 UI 의미를 파악하기 어려워진다.

## 📋 쉽게 풀어 쓴 점검 결과

| 항목 | 무엇이 문제인가 (쉬운 설명) | 안 고치면 |
|---|---|---|
| C10 | 코드와 안내문에 성구·암송 같은 서비스 목적이 그대로 보입니다. 실제 본문은 이미 코드 밖으로 옮겨져 있습니다. | 배포는 가능하나, 코드를 외부에 공개·공유하면 이 용어들도 함께 노출됩니다. |

## 📎 이대로 복사해서 다시 입력하세요

C10은 꼭 고치지 않아도 배포할 수 있습니다. 다만 코드를 공개·공유할 때
서비스 목적과 관련 용어가 함께 노출됩니다. 완전한 중립화를 원하면 다음과 같이
요청하세요.

```text
C10을 고치고 싶어.
사용자에게 보이는 버튼·안내문은 data/ui_labels.json으로 외부화하고,
추적되는 Python 코드의 종교 관련 파일명·상수명·변수명은 중립적인 이름으로 바꿔줘.
실제 라벨 파일은 data/ 아래에 두어 Git에 포함되지 않게 하고,
기존 기능과 callback_data, DB 데이터 호환성은 유지해줘.
```
