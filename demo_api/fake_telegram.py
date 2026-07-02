"""실제 텔레그램 네트워크 없이 bot.py의 핸들러를 그대로 호출하기 위한 가짜 객체들.

python-telegram-bot 자체 테스트 스위트에서 쓰는 것과 같은 방식 — Bot의 전송 메서드를
오버라이드해 실제 HTTP 대신 리스트에 기록하고, Message/CallbackQuery는 set_bot()으로
그 가짜 Bot에 묶어서 handler 코드가 reply_text()/edit_message_text()를 그대로 호출해도
동작하게 만든다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from telegram import Chat, InlineKeyboardMarkup, Message, MessageEntity, Update, User
from telegram.ext import CallbackContext, ExtBot

# 실제 네트워크 호출을 절대 하지 않으므로 진짜 토큰일 필요 없음 — 형식만 맞춘 값.
FAKE_TOKEN = "123456789:AAFakeFakeFakeFakeFakeFakeFakeFakeFA"


def _extract_buttons(reply_markup: Any) -> list[dict[str, str]]:
    if not isinstance(reply_markup, InlineKeyboardMarkup):
        return []
    out: list[dict[str, str]] = []
    for row in reply_markup.inline_keyboard:
        for btn in row:
            out.append({"label": btn.text, "callback_data": btn.callback_data or ""})
    return out


class RecordingBot(ExtBot):
    """send_message/edit_message_text/answer_callback_query를 가로채 기록만 하는 Bot."""

    def __init__(self) -> None:
        super().__init__(token=FAKE_TOKEN)
        # TelegramObject는 밑줄로 시작하지 않는 속성은 생성 후 못 바꾸게 막아둠(frozen) — 그래서
        # 내부 상태는 전부 밑줄 접두 이름으로 두고 sent 프로퍼티로만 공개한다.
        self._sent: list[dict[str, Any]] = []
        self._next_message_id = 1

    @property
    def sent(self) -> list[dict[str, Any]]:
        return self._sent

    def _record(self, chat_id: int, text: str, reply_markup: Any, parse_mode: Any) -> Message:
        self._sent.append({
            "text": text,
            "buttons": _extract_buttons(reply_markup),
            # bot.py는 사용자 입력을 항상 html.escape()로 이스케이프한 뒤 <b><u> 등으로 감싸서
            # parse_mode="HTML"로 보낸다 — 프론트는 이 값이 "HTML"일 때만 HTML로 렌더링해야 한다.
            "parse_mode": str(parse_mode) if parse_mode else None,
        })
        message_id = self._next_message_id
        self._next_message_id += 1
        chat = Chat(id=chat_id, type=Chat.PRIVATE)
        message = Message(
            message_id=message_id,
            date=datetime.now(),
            chat=chat,
            text=text,
        )
        message.set_bot(self)
        return message

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None, **kwargs) -> Message:  # type: ignore[override]
        return self._record(int(chat_id), text, reply_markup, parse_mode)

    async def edit_message_text(  # type: ignore[override]
        self, text, chat_id=None, message_id=None, inline_message_id=None, reply_markup=None, parse_mode=None, **kwargs
    ) -> Message:
        return self._record(int(chat_id) if chat_id is not None else 0, text, reply_markup, parse_mode)

    async def answer_callback_query(self, callback_query_id, text=None, **kwargs) -> bool:  # type: ignore[override]
        return True


class DemoApplication:
    """CallbackContext가 요구하는 최소 Application 인터페이스만 흉내내는 스텁.

    CallbackContext.user_data/.bot 프로퍼티가 각각
    `self._application.user_data[self._user_id]` / `self._application.bot` 을 그대로 읽으므로
    (실제 PTB 21.6 소스로 확인함), 여기서 그 두 속성만 채워주면 된다.
    job_queue=None이면 schedule_daily_reminder() 등이 알아서 안전하게 no-op 처리한다(bot.py:659-660).
    """

    job_queue = None

    def __init__(self, bot: RecordingBot, user_data_store: dict[int, dict]) -> None:
        self.bot = bot
        self.user_data = user_data_store


def make_demo_user(user_id: int) -> User:
    return User(id=user_id, is_bot=False, first_name="데모사용자")


def make_command_update(bot: RecordingBot, chat_id: int, user_id: int, update_id: int, command: str) -> Update:
    chat = Chat(id=chat_id, type=Chat.PRIVATE)
    user = make_demo_user(user_id)
    message = Message(
        message_id=update_id,
        date=datetime.now(),
        chat=chat,
        from_user=user,
        text=command,
        entities=[MessageEntity(type=MessageEntity.BOT_COMMAND, offset=0, length=len(command))],
    )
    message.set_bot(bot)
    update = Update(update_id=update_id, message=message)
    return update


def make_text_update(bot: RecordingBot, chat_id: int, user_id: int, update_id: int, text: str) -> Update:
    chat = Chat(id=chat_id, type=Chat.PRIVATE)
    user = make_demo_user(user_id)
    message = Message(message_id=update_id, date=datetime.now(), chat=chat, from_user=user, text=text)
    message.set_bot(bot)
    update = Update(update_id=update_id, message=message)
    return update


def make_callback_update(
    bot: RecordingBot, chat_id: int, user_id: int, update_id: int, callback_data: str, message_id: int
) -> Update:
    from telegram import CallbackQuery

    chat = Chat(id=chat_id, type=Chat.PRIVATE)
    user = make_demo_user(user_id)
    origin_message = Message(message_id=message_id, date=datetime.now(), chat=chat, text="")
    origin_message.set_bot(bot)
    query = CallbackQuery(
        id=str(update_id),
        from_user=user,
        chat_instance=str(chat_id),
        data=callback_data,
        message=origin_message,
    )
    query.set_bot(bot)
    update = Update(update_id=update_id, callback_query=query)
    return update


def make_context(
    bot: RecordingBot, user_id: int, user_data_store: dict[int, dict]
) -> CallbackContext:
    """세션별 user_data는 user_data_store(session이 들고 있는 dict)에 유지해 실제 봇처럼
    /start 이후에도 conversation 진행상태(quiz 등)가 이어지게 한다."""
    user_data_store.setdefault(user_id, {})
    application = DemoApplication(bot, user_data_store)
    return CallbackContext(application=application, user_id=user_id)  # type: ignore[arg-type]
