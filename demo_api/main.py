"""성구 암송 봇 — 실제 bot.py 핸들러를 그대로 실행하는 라이브 데모 API.

실제 텔레그램 네트워크를 전혀 쓰지 않는다 (fake_telegram.py 참고). 운영 봇(../bot.py, 실제 폴링
프로세스)과는 완전히 분리된 SQLite 파일을 쓴다 — 이 프로세스가 bot 모듈을 import하기 전에
BOT_DB_PATH를 데모 전용 경로로 오버라이드하기 때문에 운영 데이터에는 절대 손대지 않는다.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

# bot.py는 모듈 최상단에서 `DB_FILE = Path(os.getenv("BOT_DB_PATH", ...))`를 읽으므로,
# bot을 import하기 전에 반드시 이 환경변수부터 설정해야 한다 — 순서 바꾸면 운영 DB를 건드리게 됨.
os.environ.setdefault("BOT_DB_PATH", str(Path(__file__).parent / "demo_data" / "demo.sqlite3"))

import sys  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent))  # bot.py가 있는 상위 폴더를 import 경로에 추가

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import bot as bot_module  # noqa: E402  — 실제 운영 핸들러 코드를 그대로 재사용
from demo_api.fake_telegram import (  # noqa: E402
    RecordingBot,
    make_callback_update,
    make_command_update,
    make_context,
    make_text_update,
)

ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]

app = FastAPI(title="scripture-memory-bot demo api")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# 세션 상태 — 전부 프로세스 메모리에만 존재, 컨테이너 재시작하면 사라짐(의도된 동작, 개인정보 안 쌓임).
USER_DATA_STORE: dict[int, dict] = {}
SESSION_TO_USER_ID: dict[str, int] = {}
_NEXT_USER_ID = [900_000_001]  # 실제 텔레그램 user_id와 겹치지 않게 큰 값에서 시작

_RATE_LIMIT_WINDOW_SECONDS = 10
_RATE_LIMIT_MAX_REQUESTS = 20
_rate_buckets: dict[str, list[float]] = {}


def _check_rate_limit(client_ip: str) -> None:
    now = time.time()
    bucket = [t for t in _rate_buckets.get(client_ip, []) if now - t < _RATE_LIMIT_WINDOW_SECONDS]
    if len(bucket) >= _RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(status_code=429, detail="요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.")
    bucket.append(now)
    _rate_buckets[client_ip] = bucket


def _session_user_id(session_id: str) -> int:
    if session_id not in SESSION_TO_USER_ID:
        SESSION_TO_USER_ID[session_id] = _NEXT_USER_ID[0]
        _NEXT_USER_ID[0] += 1
    return SESSION_TO_USER_ID[session_id]


class ChatRequest(BaseModel):
    session_id: str
    text: str | None = None
    callback_data: str | None = None


class ChatMessage(BaseModel):
    text: str
    buttons: list[dict[str, str]]
    parse_mode: str | None = None


class ChatResponse(BaseModel):
    messages: list[ChatMessage]


@app.on_event("startup")
def on_startup() -> None:
    bot_module.init_database()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


async def _run_update(user_id: int, chat_id: int, *, command: str | None, text: str | None, callback_data: str | None):
    bot = RecordingBot()
    context = make_context(bot, user_id, USER_DATA_STORE)
    update_id = int(time.time() * 1000) % 2_000_000_000

    if command is not None:
        update = make_command_update(bot, chat_id, user_id, update_id, command)
        await bot_module.start(update, context)
    elif callback_data is not None:
        update = make_callback_update(bot, chat_id, user_id, update_id, callback_data, message_id=update_id)
        await bot_module.handle_button(update, context)
    else:
        update = make_text_update(bot, chat_id, user_id, update_id, text or "")
        await bot_module.handle_answer(update, context)

    return bot.sent


@app.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    _check_rate_limit(request.client.host if request.client else "unknown")
    if not payload.session_id or len(payload.session_id) > 128:
        raise HTTPException(status_code=400, detail="잘못된 session_id 입니다.")

    user_id = _session_user_id(payload.session_id)
    chat_id = user_id  # 1:1 개인 대화라 실제 봇처럼 user_id == chat_id로 취급해도 무방

    is_first_turn = user_id not in USER_DATA_STORE
    command = "/start" if (is_first_turn and payload.text is None and payload.callback_data is None) else None

    sent = await _run_update(
        user_id,
        chat_id,
        command=command,
        text=payload.text,
        callback_data=payload.callback_data,
    )
    return ChatResponse(messages=[ChatMessage(**m) for m in sent])


@app.post("/reset", response_model=ChatResponse)
async def reset(payload: ChatRequest) -> ChatResponse:
    if not payload.session_id or len(payload.session_id) > 128:
        raise HTTPException(status_code=400, detail="잘못된 session_id 입니다.")
    user_id = _session_user_id(payload.session_id)
    USER_DATA_STORE.pop(user_id, None)
    sent = await _run_update(user_id, user_id, command="/start", text=None, callback_data=None)
    return ChatResponse(messages=[ChatMessage(**m) for m in sent])


@app.post("/session")
def new_session() -> dict[str, str]:
    return {"session_id": uuid.uuid4().hex}
