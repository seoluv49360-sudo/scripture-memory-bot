import asyncio
import difflib
import html
import json
import os
import random
import re
import sqlite3
import sys
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from scriptures import SCRIPTURE_BY_ID, SCRIPTURES


MODE_FULL = "full"
MODE_BLANK = "blank"
SCRIPTURE_PLACEHOLDER = "개역한글 본문을 여기에 입력해 주세요."
REMINDER_FILE = Path("reminders.json")
DB_FILE = Path(os.getenv("BOT_DB_PATH", "data/bot.sqlite3"))
DB_LOCK = threading.RLock()
QUIZ_STATE_TTL_DAYS = int(os.getenv("QUIZ_STATE_TTL_DAYS", "7"))
ERROR_LOG_TTL_DAYS = int(os.getenv("ERROR_LOG_TTL_DAYS", "14"))
try:
    KST = ZoneInfo("Asia/Seoul")
except Exception:
    KST = timezone(timedelta(hours=9), name="KST")

REMINDER_TIME = time(hour=8, minute=0, tzinfo=KST)

DIFFICULTIES = {
    "easy": {"label": "하", "ratio": 0.12, "max_blanks": 4, "hint": "가볍게 확인", "subjective": False},
    "medium": {"label": "중", "ratio": 0.2, "max_blanks": 7, "hint": "암송 점검", "subjective": False},
    "hard": {"label": "상", "ratio": 0.3, "max_blanks": 10, "hint": "실전 훈련", "subjective": False},
    "expert": {"label": "최상", "ratio": 0.2, "max_blanks": 7, "hint": "주관식 도전", "subjective": True},
}

SUBJECTIVE_MIN_GAP_WORDS = 2

NUMBER_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


@dataclass
class QuizState:
    scripture_id: str
    mode: str
    answers: list[str]
    difficulty: str | None = None
    quiz_text: str = ""
    quiz_words: list[str] = field(default_factory=list)
    blank_indexes: list[int] = field(default_factory=list)
    blank_suffixes: list[str] = field(default_factory=list)
    options: list[str] = field(default_factory=list)
    selected_indexes: list[int] = field(default_factory=list)
    typed_answers: list[str] = field(default_factory=list)
    prompt_message_id: int | None = None


def db_connect() -> sqlite3.Connection:
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_FILE)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def init_database() -> None:
    with DB_LOCK, db_connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS reminder_chats (
                chat_id INTEGER PRIMARY KEY,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS quiz_states (
                user_id INTEGER PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                update_payload TEXT,
                error TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_visitors (
                visit_date TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                first_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (visit_date, user_id)
            )
            """
        )
        connection.commit()
    migrate_reminders_json()


def migrate_reminders_json() -> None:
    if not REMINDER_FILE.exists():
        return
    try:
        data = json.loads(REMINDER_FILE.read_text(encoding="utf-8"))
        chat_ids = {int(chat_id) for chat_id in data.get("chat_ids", [])}
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return
    if not chat_ids:
        return
    save_reminder_chats(load_reminder_chats() | chat_ids)


def quiz_to_json(quiz: QuizState) -> str:
    return json.dumps(asdict(quiz), ensure_ascii=False)


def quiz_from_json(data: str) -> QuizState | None:
    try:
        payload = json.loads(data)
        return QuizState(**payload)
    except (json.JSONDecodeError, TypeError):
        return None


def state_user_id(update: Update) -> int:
    if update.effective_user:
        return update.effective_user.id
    if update.effective_chat:
        return update.effective_chat.id
    return 0


def save_quiz_state(user_id: int, quiz: QuizState) -> None:
    if not user_id:
        return
    with DB_LOCK, db_connect() as connection:
        connection.execute(
            """
            INSERT INTO quiz_states (user_id, data, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                data = excluded.data,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, quiz_to_json(quiz)),
        )
        connection.commit()


def load_quiz_state(user_id: int) -> QuizState | None:
    if not user_id:
        return None
    with DB_LOCK, db_connect() as connection:
        row = connection.execute(
            "SELECT data FROM quiz_states WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return quiz_from_json(row[0]) if row else None


def clear_quiz_state(user_id: int) -> None:
    if not user_id:
        return
    with DB_LOCK, db_connect() as connection:
        connection.execute("DELETE FROM quiz_states WHERE user_id = ?", (user_id,))
        connection.commit()


def get_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> QuizState | None:
    quiz = context.user_data.get("quiz")
    if quiz:
        return quiz
    quiz = load_quiz_state(state_user_id(update))
    if quiz:
        context.user_data["quiz"] = quiz
    return quiz


def set_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE, quiz: QuizState) -> None:
    context.user_data["quiz"] = quiz
    save_quiz_state(state_user_id(update), quiz)


def clear_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("quiz", None)
    clear_quiz_state(state_user_id(update))


def log_bot_error(update: object, error: BaseException) -> None:
    try:
        update_payload = update.to_json() if hasattr(update, "to_json") else str(update)
    except Exception:
        update_payload = "<unserializable update>"
    with DB_LOCK, db_connect() as connection:
        connection.execute(
            "INSERT INTO bot_errors (update_payload, error) VALUES (?, ?)",
            (update_payload, repr(error)),
        )
        connection.commit()


def is_ignorable_telegram_error(error: BaseException) -> bool:
    return isinstance(error, BadRequest) and "Message is not modified" in str(error)


def database_counts() -> dict[str, int]:
    today = datetime.now(KST).date().isoformat()
    with DB_LOCK, db_connect() as connection:
        reminders = connection.execute("SELECT COUNT(*) FROM reminder_chats").fetchone()[0]
        quizzes = connection.execute("SELECT COUNT(*) FROM quiz_states").fetchone()[0]
        errors = connection.execute("SELECT COUNT(*) FROM bot_errors").fetchone()[0]
        today_visitors = connection.execute(
            "SELECT COUNT(*) FROM daily_visitors WHERE visit_date = ?",
            (today,),
        ).fetchone()[0]
    return {"reminders": reminders, "quizzes": quizzes, "errors": errors, "today_visitors": today_visitors}


def record_visit(update: Update) -> None:
    if not update.effective_user:
        return
    today = datetime.now(KST).date().isoformat()
    with DB_LOCK, db_connect() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO daily_visitors (visit_date, user_id) VALUES (?, ?)",
            (today, update.effective_user.id),
        )
        connection.commit()


def cleanup_old_records() -> None:
    with DB_LOCK, db_connect() as connection:
        connection.execute(
            "DELETE FROM quiz_states WHERE updated_at < datetime('now', ?)",
            (f"-{QUIZ_STATE_TTL_DAYS} days",),
        )
        connection.execute(
            "DELETE FROM bot_errors WHERE created_at < datetime('now', ?)",
            (f"-{ERROR_LOG_TTL_DAYS} days",),
        )
        connection.commit()


def normalize(text: str) -> str:
    compact = re.sub(r"\s+", "", text.strip())
    return re.sub(r"[^\w가-힣]", "", compact).lower()


def remove_verse_numbers(text: str) -> str:
    text = re.sub(r"(?m)^\s*\d{1,3}\s*절\s*", "", text)
    text = re.sub(r"(?<=\s)\d{1,3}\s*절\s*", "", text)
    text = re.sub(r"(?m)^\s*\d{1,3}\s*[.)]?\s*", "", text)
    text = re.sub(r"(?<=\s)\d{1,3}\s*[.)]\s*", "", text)
    return re.sub(r"(?<=\s)\d{1,3}\s+", "", text)


def normalize_for_memory(text: str) -> str:
    without_numbers = remove_verse_numbers(text)
    compact = re.sub(r"\s+", "", without_numbers.strip())
    return re.sub(r"[^A-Za-z가-힣]", "", compact).lower()


def memory_tokens(text: str) -> list[str]:
    without_numbers = remove_verse_numbers(text)
    raw_tokens = re.findall(r"[A-Za-z가-힣]+", without_numbers)
    return [token for token in raw_tokens if token]


def score_answer(expected: str, submitted: str) -> tuple[int, bool]:
    expected_norm = normalize_for_memory(expected)
    submitted_norm = normalize_for_memory(submitted)
    if not expected_norm:
        return 0, False

    ratio = difflib.SequenceMatcher(None, expected_norm, submitted_norm).ratio()
    score = round(ratio * 100)
    return score, expected_norm == submitted_norm


def build_memory_diff(expected: str, submitted: str) -> str:
    expected_tokens = memory_tokens(expected)
    expected_norm = "".join(token.lower() for token in expected_tokens)
    submitted_norm = normalize_for_memory(submitted)

    bad_positions = set()
    matcher = difflib.SequenceMatcher(None, expected_norm, submitted_norm)
    for tag, expected_start, expected_end, _, _ in matcher.get_opcodes():
        if tag != "equal":
            bad_positions.update(range(expected_start, expected_end))

    cursor = 0
    marked_tokens = []
    for token in expected_tokens:
        token_length = len(token)
        token_positions = set(range(cursor, cursor + token_length))
        escaped = html.escape(token)
        if token_positions & bad_positions:
            marked_tokens.append(f"<b><u>{escaped}</u></b>")
        else:
            marked_tokens.append(escaped)
        cursor += token_length

    return " ".join(marked_tokens)


def has_scripture_text(scripture: dict[str, str]) -> bool:
    return scripture["text"].strip() != SCRIPTURE_PLACEHOLDER


def short_reference(reference: str) -> str:
    return reference.replace("요한계시록", "계")


def is_single_verse(reference: str) -> bool:
    return "-" not in reference and "~" not in reference


def format_scripture_text(text: str, reference: str | None = None) -> str:
    if reference and is_single_verse(reference):
        text = re.sub(r"^\s*\d{1,3}\s+", "", text.strip())
    formatted = re.sub(r"\s+(\d{1,2})\s+", r"\n\1 ", text).strip()
    return formatted


def scripture_keyboard(chat_id: int | None = None) -> InlineKeyboardMarkup:
    rows = []
    current_row = []
    for index, scripture in enumerate(SCRIPTURES, start=1):
        number = NUMBER_EMOJIS[index - 1] if index <= len(NUMBER_EMOJIS) else str(index)
        current_row.append(
            InlineKeyboardButton(
                f"{number} {short_reference(scripture['reference'])}",
                callback_data=f"scripture:{scripture['id']}",
            )
        )
        if len(current_row) == 2:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)
    if chat_id is None or chat_id not in load_reminder_chats():
        rows.append(
            [
                InlineKeyboardButton("🔔 매일 8시 리마인더 받기", callback_data="reminder:on"),
            ]
        )
    return InlineKeyboardMarkup(rows)


def select_scripture_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("📖 암송할 성구 선택하기", callback_data="menu")]]
    )


def reminder_start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("📖 성구 암송하러 가기", callback_data="menu")]]
    )


def load_reminder_chats() -> set[int]:
    with DB_LOCK, db_connect() as connection:
        rows = connection.execute("SELECT chat_id FROM reminder_chats").fetchall()
    return {int(row[0]) for row in rows}


def save_reminder_chats(chat_ids: set[int]) -> None:
    with DB_LOCK, db_connect() as connection:
        connection.execute("DELETE FROM reminder_chats")
        connection.executemany(
            "INSERT OR IGNORE INTO reminder_chats (chat_id) VALUES (?)",
            [(int(chat_id),) for chat_id in chat_ids],
        )
        connection.commit()


def reminder_job_name(chat_id: int) -> str:
    return f"daily_scripture_reminder:{chat_id}"


def schedule_daily_reminder(application: Application, chat_id: int) -> bool:
    if application.job_queue is None:
        return False

    for job in application.job_queue.get_jobs_by_name(reminder_job_name(chat_id)):
        job.schedule_removal()

    application.job_queue.run_daily(
        send_daily_reminder,
        time=REMINDER_TIME,
        chat_id=chat_id,
        name=reminder_job_name(chat_id),
    )
    return True


def schedule_saved_reminders(application: Application) -> None:
    for chat_id in load_reminder_chats():
        schedule_daily_reminder(application, chat_id)


async def send_daily_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    scripture = random.choice(SCRIPTURES)
    await context.bot.send_message(
        chat_id=context.job.chat_id,
        text=(
            "🌅 오늘의 암송 리마인더\n\n"
            f"📌 {scripture['reference']}\n\n"
            f"{format_scripture_text(scripture['text'], scripture['reference'])}\n\n"
            "✍️ 조용히 한 번 읽고, 눈을 감고 다시 떠올려 보세요."
        ),
        reply_markup=reminder_start_keyboard(),
    )


def mode_keyboard(scripture_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✍️ 전체 암기 시작", callback_data=f"mode:{MODE_FULL}:{scripture_id}")],
            [InlineKeyboardButton("🧩 빈칸 넣기", callback_data=f"mode:{MODE_BLANK}:{scripture_id}")],
            [InlineKeyboardButton("📖 다른 성구 선택", callback_data="menu")],
        ]
    )


def practice_back_keyboard(scripture_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⬅️ 연습 방식 선택", callback_data=f"scripture:{scripture_id}")],
            [InlineKeyboardButton("📖 다른 성구 선택", callback_data="menu")],
        ]
    )


def difficulty_keyboard(scripture_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🟢 하", callback_data=f"blank:easy:{scripture_id}"),
                InlineKeyboardButton("🟡 중", callback_data=f"blank:medium:{scripture_id}"),
                InlineKeyboardButton("🔴 상", callback_data=f"blank:hard:{scripture_id}"),
            ],
            [InlineKeyboardButton("⚫ 최상 · 주관식", callback_data=f"blank:expert:{scripture_id}")],
            [InlineKeyboardButton("👀 성구 보기", callback_data=f"scripture:{scripture_id}")],
            [InlineKeyboardButton("📖 다른 성구 선택", callback_data="menu")],
        ]
    )


def full_result_keyboard(scripture_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔁 전체 암기 다시 도전", callback_data=f"mode:{MODE_FULL}:{scripture_id}")],
            [InlineKeyboardButton("🧩 빈칸으로 연습", callback_data=f"mode:{MODE_BLANK}:{scripture_id}")],
            [InlineKeyboardButton("📖 다른 성구 선택", callback_data="menu")],
        ]
    )


def blank_result_keyboard(scripture_id: str, difficulty: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔁 같은 난이도 다시 풀기", callback_data=f"blank:{difficulty}:{scripture_id}")],
            [InlineKeyboardButton("🎚️ 난이도 변경", callback_data=f"mode:{MODE_BLANK}:{scripture_id}")],
            [InlineKeyboardButton("✍️ 전체 암기 도전", callback_data=f"mode:{MODE_FULL}:{scripture_id}")],
            [InlineKeyboardButton("📖 다른 성구 선택", callback_data="menu")],
        ]
    )


def blank_choice_keyboard(quiz: QuizState) -> InlineKeyboardMarkup:
    if DIFFICULTIES.get(quiz.difficulty or "easy", {}).get("subjective"):
        rows = []
        if len(quiz.typed_answers) >= len(quiz.answers):
            rows.append([InlineKeyboardButton("✅ 채점하기", callback_data="blank_subjective_submit")])
        if quiz.typed_answers:
            rows.append([InlineKeyboardButton("↩️ 방금 입력 취소", callback_data="blank_subjective_undo")])
        rows.append([InlineKeyboardButton("↩️ 입력 초기화", callback_data="blank_reset")])
        rows.append([InlineKeyboardButton("⬅️ 연습 방식 선택", callback_data=f"scripture:{quiz.scripture_id}")])
        return InlineKeyboardMarkup(rows)

    if len(quiz.selected_indexes) >= len(quiz.answers):
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("✅ 채점하기", callback_data="blank_submit")],
                [InlineKeyboardButton("↩️ 방금 선택 취소", callback_data="blank_undo")],
                [InlineKeyboardButton("⬅️ 연습 방식 선택", callback_data=f"scripture:{quiz.scripture_id}")],
            ]
        )

    available_buttons = []
    selected = set(quiz.selected_indexes)
    for index, option in enumerate(quiz.options):
        if index not in selected:
            available_buttons.append(InlineKeyboardButton(f"🔹 {option}", callback_data=f"pick:{index}"))

    rows = [available_buttons[index : index + 2] for index in range(0, len(available_buttons), 2)]
    if quiz.selected_indexes:
        rows.append([InlineKeyboardButton("↩️ 방금 선택 취소", callback_data="blank_undo")])
    rows.append([InlineKeyboardButton("↩️ 선택 초기화", callback_data="blank_reset")])
    rows.append([InlineKeyboardButton("⬅️ 연습 방식 선택", callback_data=f"scripture:{quiz.scripture_id}")])
    rows.append([InlineKeyboardButton("🎚️ 난이도 변경", callback_data=f"mode:{MODE_BLANK}:{quiz.scripture_id}")])
    return InlineKeyboardMarkup(rows)


def blank_answer_for_slot(quiz: QuizState, slot_index: int) -> str | None:
    if DIFFICULTIES.get(quiz.difficulty or "easy", {}).get("subjective"):
        if slot_index >= len(quiz.typed_answers):
            return None
        return quiz.typed_answers[slot_index]
    if slot_index >= len(quiz.selected_indexes):
        return None
    return quiz.options[quiz.selected_indexes[slot_index]]


def render_blank_text(quiz: QuizState) -> str:
    blank_slots = {word_index: slot_index for slot_index, word_index in enumerate(quiz.blank_indexes)}
    rendered_words = []
    for word_index, word in enumerate(quiz.quiz_words):
        if word_index not in blank_slots:
            rendered_words.append(html.escape(word))
            continue

        slot_index = blank_slots[word_index]
        suffix = quiz.blank_suffixes[slot_index] if slot_index < len(quiz.blank_suffixes) else ""
        selected_answer = blank_answer_for_slot(quiz, slot_index)
        if selected_answer:
            rendered_words.append(f"(<b><u>{html.escape(selected_answer)}</u></b>){html.escape(suffix)}")
        else:
            blank_number = slot_index + 1
            rendered_words.append(f"({blank_number}) {'_' * 6}{html.escape(suffix)}")
    rendered_text = " ".join(rendered_words)
    return re.sub(r"\s+(\d{1,2})\s+", r"\n\1 ", rendered_text).strip()


def blank_prompt_text(scripture: dict[str, str], quiz: QuizState) -> str:
    difficulty = DIFFICULTIES[quiz.difficulty or "easy"]
    total = len(quiz.answers)
    if difficulty.get("subjective"):
        filled_count = len(quiz.typed_answers)
        if filled_count >= total:
            action_text = "✅ 모두 채웠습니다. 채점하기를 눌러 결과를 확인하세요."
        else:
            action_text = f"✍️ {filled_count + 1}번 빈칸에 들어갈 답을 입력하고 엔터를 누르세요."

        return (
            f"🧩 {scripture['reference']} 빈칸 넣기\n"
            f"🎚️ 난이도: {difficulty['label']} · {difficulty['hint']}\n\n"
            f"{render_blank_text(quiz)}\n\n"
            f"{action_text}\n"
            f"📍 진행: {filled_count}/{total}"
        )

    if len(quiz.selected_indexes) >= total:
        action_text = "✅ 모두 채웠습니다. 채점하기를 눌러 결과를 확인하세요."
    else:
        next_number = len(quiz.selected_indexes) + 1
        action_text = f"👉 지금은 {next_number}번 빈칸 차례입니다."

    return (
        f"🧩 {scripture['reference']} 빈칸 넣기\n"
        f"🎚️ 난이도: {difficulty['label']} · {difficulty['hint']}\n\n"
        f"{render_blank_text(quiz)}\n\n"
        f"{action_text}\n"
        f"📍 진행: {len(quiz.selected_indexes)}/{total}"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    record_visit(update)
    clear_quiz(update, context)
    context.user_data.clear()
    await update.message.reply_text(
        "📖 암송할 성구를 선택하세요.\n\n원하는 구절을 누르면 연습 방식을 고를 수 있습니다.",
        reply_markup=scripture_keyboard(update.effective_chat.id),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    record_visit(update)
    await update.message.reply_text(
        "📖 /start - 성구 선택\n"
        "🔔 /remind_on - 매일 오전 8시 랜덤 성구 받기\n"
        "📊 /status - 봇 상태 확인\n"
        "🛑 /cancel - 현재 문제 취소\n\n"
        "✍️ 전체 암기는 입력한 문장을 원문과 비교해 점수를 보여 줍니다.\n"
        "🧩 빈칸 넣기는 보기 버튼을 빈칸 순서대로 누르면 자동으로 채점됩니다."
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    record_visit(update)
    counts = database_counts()
    await update.message.reply_text(
        "📊 봇 상태\n\n"
        f"👥 오늘 방문자: {counts['today_visitors']}명\n"
        f"🔔 리마인더 등록 채팅: {counts['reminders']}개\n"
        f"🧩 진행 중인 퀴즈 상태: {counts['quizzes']}개\n"
        f"⚠️ 기록된 에러: {counts['errors']}개\n"
        "✅ 봇 프로세스가 응답 중입니다."
    )


async def remind_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    record_visit(update)
    chat_id = update.effective_chat.id
    if not schedule_daily_reminder(context.application, chat_id):
        await update.message.reply_text(
            "⚠️ 리마인더 예약 기능을 사용할 수 없습니다.\n\n"
            "아래 명령으로 패키지를 다시 설치한 뒤 봇을 재실행해 주세요.\n"
            ".\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt"
        )
        return

    chat_ids = load_reminder_chats()
    chat_ids.add(chat_id)
    save_reminder_chats(chat_ids)
    await update.message.reply_text(
        "🔔 매일 오전 8시에 랜덤 암송 성구 1개를 보내드릴게요.",
        reply_markup=select_scripture_keyboard(),
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    record_visit(update)
    clear_quiz(update, context)
    await update.message.reply_text(
        "🛑 현재 문제를 취소했습니다.\n\n다시 성구를 골라 주세요.",
        reply_markup=scripture_keyboard(update.effective_chat.id),
    )


PARTICLE_SUFFIXES = (
    "으로부터",
    "께로부터",
    "로부터",
    "밖에는",
    "밖에",
    "에게",
    "께서",
    "으로",
    "이",
    "가",
    "을",
    "를",
    "과",
    "와",
    "도",
    "에",
    "로",
)

NUMBER_WORDS = {
    "일",
    "이",
    "삼",
    "사",
    "오",
    "육",
    "칠",
    "팔",
    "구",
    "십",
    "백",
    "천",
    "만",
    "십사만",
    "사천",
}

STANDALONE_BLANK_STOPWORDS = {
    "곧",
    "이는",
    "나의",
    "내",
    "네가",
    "그",
    "저가",
    "또",
    "및",
    "그이",
}

PHRASE_PREFIX_STOPWORDS = {"곧", "이는", "또", "및", "그", "그이", "내", "나의", "네가", "저가"}
PHRASE_STEM_STOPWORDS = {"그", "내", "나", "네", "저", "이"}
CLAUSE_ENDINGS = ("더라", "니라", "리라", "도다", "으매", "하니", "리니", "라", "니", "며", "매", "고", "되", "요")


def is_standalone_stopword(word: str) -> bool:
    return normalize(word) in STANDALONE_BLANK_STOPWORDS


def is_phrase_prefix_stopword(word: str) -> bool:
    return normalize(word) in PHRASE_PREFIX_STOPWORDS


def is_phrase_stem_stopword(word: str) -> bool:
    return normalize(word) in PHRASE_STEM_STOPWORDS


def split_particle(word: str) -> tuple[str, str]:
    for suffix in PARTICLE_SUFFIXES:
        if word.endswith(suffix) and len(normalize(word[: -len(suffix)])) >= 1:
            return word[: -len(suffix)], suffix
    return word, ""


def is_number_word(word: str) -> bool:
    normalized = normalize(word)
    korean_chars = re.findall(r"[가-힣]", normalized)
    return bool(korean_chars) and all(char in NUMBER_WORDS for char in korean_chars)


def is_numeric_token(word: str) -> bool:
    return bool(re.fullmatch(r"\d+[.)]?", normalize(word)))


def is_blankable_word(word: str) -> bool:
    if is_numeric_token(word):
        return False
    if is_standalone_stopword(word):
        return False
    stem, suffix = split_particle(word)
    return not suffix and len(normalize(stem)) >= 2


def phrase_start_for_complete_unit(words: list[str], index: int, stem: str) -> int | None:
    start = index - 1
    previous_stem, previous_suffix = split_particle(words[index - 1])
    if (
        index >= 2
        and words[index - 2].endswith("의")
        and not previous_suffix
        and not normalize(words[index - 1]).endswith(CLAUSE_ENDINGS)
    ):
        start = index - 2
    while start < index and is_numeric_token(words[start]):
        start += 1
    if start >= index:
        return index if len(normalize(stem)) >= 2 else None
    if is_phrase_prefix_stopword(words[start]):
        return index if len(normalize(stem)) >= 2 else None
    if normalize(words[start]).endswith(CLAUSE_ENDINGS):
        return index if len(normalize(stem)) >= 2 else None
    previous_stem, previous_suffix = split_particle(words[start])
    if previous_suffix and not words[start].endswith("의"):
        return index if len(normalize(stem)) >= 2 else None
    if len(normalize(stem)) < 2 and not (index >= 2 and words[index - 2].endswith("의")):
        return None
    return start


def make_candidate(
    words: list[str],
    start: int,
    end: int,
    answer_words: list[str],
    suffix: str,
    kind: str,
    priority: int,
) -> dict:
    answer = " ".join(answer_words)
    return {
        "start": start,
        "end": end,
        "answer": answer,
        "suffix": suffix,
        "kind": kind,
        "priority": priority,
    }


def has_numeric_token(text: str) -> bool:
    return any(is_numeric_token(word) for word in text.split())


def make_blank_quiz(text: str, difficulty: str) -> tuple[str, list[str], list[str], list[int], list[str]]:
    words = text.split()
    difficulty_info = DIFFICULTIES[difficulty]
    if len(words) < 4:
        blank_count = 1
    else:
        ratio_count = max(1, round(len(words) * difficulty_info["ratio"]))
        blank_count = min(ratio_count, difficulty_info["max_blanks"])

    candidates = []
    for index, word in enumerate(words):
        stem, suffix = split_particle(word)
        if (
            suffix in {"밖에는", "밖에"}
            and index >= 2
            and len(normalize(stem)) >= 1
            and is_number_word(words[index - 2])
            and is_number_word(words[index - 1])
        ):
            candidates.append(
                make_candidate(
                    words,
                    index - 2,
                    index + 1,
                    [words[index - 2], words[index - 1], words[index]],
                    "",
                    "number_phrase",
                    1,
                )
            )
        elif (
            word.endswith("같이")
            and index > 0
            and index + 1 < len(words)
            and not is_numeric_token(words[index - 1])
            and not is_phrase_prefix_stopword(words[index - 1])
        ):
            candidates.append(
                make_candidate(
                    words,
                    index - 1,
                    index + 2,
                    [words[index - 1], word, words[index + 1]],
                    "",
                    "adverb_phrase",
                    1,
                )
            )
        elif (
            suffix
            and index > 0
            and len(normalize(stem)) >= 1
            and len(normalize(words[index - 1])) >= 1
            and not is_number_word(stem)
            and not is_phrase_stem_stopword(stem)
        ):
            start = phrase_start_for_complete_unit(words, index, stem)
            if start is None:
                continue
            answer_words = words[start : index + 1]
            candidates.append(
                make_candidate(
                    words,
                    start,
                    index + 1,
                    answer_words,
                    "",
                    "complete_phrase",
                    1,
                )
            )
        elif is_blankable_word(word):
            candidates.append(
                make_candidate(words, index, index + 1, [word], "", "word", 2)
            )

    random.shuffle(candidates)
    candidates.sort(key=lambda candidate: candidate["priority"])
    selected_units = []
    used_indexes = set()
    subjective_mode = bool(difficulty_info.get("subjective"))
    for candidate in candidates:
        if has_numeric_token(candidate["answer"]):
            continue
        indexes = set(range(candidate["start"], candidate["end"]))
        if indexes & used_indexes:
            continue
        if subjective_mode:
            touches_existing_blank = any(
                candidate["start"] <= unit["end"] + SUBJECTIVE_MIN_GAP_WORDS
                and unit["start"] <= candidate["end"] + SUBJECTIVE_MIN_GAP_WORDS
                for unit in selected_units
            )
            if touches_existing_blank:
                continue
        selected_units.append(candidate)
        used_indexes.update(indexes)
        if len(selected_units) >= blank_count:
            break

    selected_units.sort(key=lambda candidate: candidate["start"])

    answers = []
    quiz_words = words[:]
    blank_indexes = []
    blank_suffixes = []
    for unit in selected_units:
        answers.append(unit["answer"])
        blank_indexes.append(unit["start"])
        blank_suffixes.append(unit["suffix"])
        quiz_words[unit["start"]] = f"({len(answers)}) {'_' * 6}"
        for index in range(unit["start"] + 1, unit["end"]):
            quiz_words[index] = ""

    old_blank_indexes = blank_indexes
    compacted_words = []
    old_to_new_indexes = {}
    for index, word in enumerate(quiz_words):
        if not word:
            continue
        old_to_new_indexes[index] = len(compacted_words)
        compacted_words.append(word)

    quiz_words = compacted_words
    blank_indexes = [old_to_new_indexes[index] for index in old_blank_indexes]

    return " ".join(quiz_words), answers, quiz_words, blank_indexes, blank_suffixes


def make_blank_options(answers: list[str]) -> list[str]:
    options = answers[:]
    random.shuffle(options)
    return options


def build_blank_result(quiz: QuizState) -> tuple[int, list[str]]:
    selected_answers = [quiz.options[index] for index in quiz.selected_indexes]
    correct_count = 0
    result_lines = []
    for index, expected in enumerate(quiz.answers, start=1):
        user_answer = selected_answers[index - 1] if index - 1 < len(selected_answers) else ""
        _, is_exact = score_answer(expected, user_answer)
        if is_exact:
            correct_count += 1
            result_lines.append(f"{index}. ✅ {expected}")
        else:
            result_lines.append(f"{index}. ❌ 정답: {expected} / 선택: {user_answer or '-'}")
    return correct_count, result_lines


def build_subjective_blank_result(quiz: QuizState) -> tuple[int, list[str]]:
    correct_count = 0
    result_lines = []
    for index, expected in enumerate(quiz.answers, start=1):
        user_answer = quiz.typed_answers[index - 1] if index - 1 < len(quiz.typed_answers) else ""
        _, is_exact = score_answer(expected, user_answer)
        if is_exact:
            correct_count += 1
            result_lines.append(f"{index}. ✅ {expected}")
        else:
            result_lines.append(f"{index}. ❌ 정답: {expected} / 입력: {user_answer or '-'}")
    return correct_count, result_lines


def blank_result_message(correct_count: int, total: int, result_lines: list[str]) -> str:
    percent = round(correct_count / total * 100) if total else 0
    if percent == 100:
        message = "🎉 모든 빈칸을 맞혔습니다."
    elif percent >= 70:
        message = "👍 좋습니다. 틀린 부분만 다시 보면 금방 잡힙니다."
    else:
        message = "🌱 괜찮아요. 같은 난이도로 한 번 더 풀어 보세요."

    return (
        f"{message}\n\n"
        f"📊 결과: {correct_count}/{total} ({percent}점)\n\n"
        + "\n".join(result_lines)
    )


async def finish_blank_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE, quiz: QuizState) -> None:
    query = update.callback_query
    correct_count, result_lines = build_blank_result(quiz)
    total = len(quiz.answers)
    clear_quiz(update, context)
    await query.edit_message_text(
        blank_result_message(correct_count, total, result_lines),
        reply_markup=blank_result_keyboard(quiz.scripture_id, quiz.difficulty or "easy"),
    )


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    record_visit(update)
    query = update.callback_query
    await query.answer()

    data = query.data
    if data == "menu":
        clear_quiz(update, context)
        await query.edit_message_text(
            "📖 암송할 성구를 선택하세요.",
            reply_markup=scripture_keyboard(query.message.chat_id),
        )
        return

    if data.startswith("reminder:"):
        action = data.split(":", 1)[1]
        chat_id = query.message.chat_id
        if action == "on":
            if not schedule_daily_reminder(context.application, chat_id):
                await query.edit_message_text(
                    "⚠️ 리마인더 예약 기능을 사용할 수 없습니다.\n\n"
                    "아래 명령으로 패키지를 다시 설치한 뒤 봇을 재실행해 주세요.\n"
                    ".\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt",
                    reply_markup=scripture_keyboard(chat_id),
                )
                return

            chat_ids = load_reminder_chats()
            chat_ids.add(chat_id)
            save_reminder_chats(chat_ids)
            await query.edit_message_text(
                "🔔 매일 오전 8시에 랜덤 암송 성구 1개를 보내드릴게요.",
                reply_markup=select_scripture_keyboard(),
            )
            return

    if data == "blank_reset":
        quiz = get_quiz(update, context)
        if not quiz or quiz.mode != MODE_BLANK:
            await query.edit_message_text(
                "🧩 진행 중인 빈칸 문제가 없습니다.",
                reply_markup=scripture_keyboard(query.message.chat_id),
            )
            return

        if DIFFICULTIES.get(quiz.difficulty or "easy", {}).get("subjective"):
            quiz.typed_answers.clear()
            set_quiz(update, context, quiz)
            await query.edit_message_text(
                blank_prompt_text(SCRIPTURE_BY_ID[quiz.scripture_id], quiz),
                reply_markup=blank_choice_keyboard(quiz),
                parse_mode="HTML",
            )
            return

        quiz.selected_indexes.clear()
        set_quiz(update, context, quiz)
        scripture = SCRIPTURE_BY_ID[quiz.scripture_id]
        await query.edit_message_text(
            blank_prompt_text(scripture, quiz),
            reply_markup=blank_choice_keyboard(quiz),
            parse_mode="HTML",
        )
        return

    if data == "blank_subjective_undo":
        quiz = get_quiz(update, context)
        if not quiz or quiz.mode != MODE_BLANK:
            await query.edit_message_text(
                "🧩 진행 중인 빈칸 문제가 없습니다.",
                reply_markup=scripture_keyboard(query.message.chat_id),
            )
            return

        if quiz.typed_answers:
            quiz.typed_answers.pop()
            set_quiz(update, context, quiz)

        scripture = SCRIPTURE_BY_ID[quiz.scripture_id]
        await query.edit_message_text(
            blank_prompt_text(scripture, quiz),
            reply_markup=blank_choice_keyboard(quiz),
            parse_mode="HTML",
        )
        return

    if data == "blank_subjective_submit":
        quiz = get_quiz(update, context)
        if not quiz or quiz.mode != MODE_BLANK:
            await query.edit_message_text(
                "🧩 진행 중인 빈칸 문제가 없습니다.",
                reply_markup=scripture_keyboard(query.message.chat_id),
            )
            return

        if len(quiz.typed_answers) < len(quiz.answers):
            scripture = SCRIPTURE_BY_ID[quiz.scripture_id]
            await query.edit_message_text(
                blank_prompt_text(scripture, quiz),
                reply_markup=blank_choice_keyboard(quiz),
                parse_mode="HTML",
            )
            return

        correct_count, result_lines = build_subjective_blank_result(quiz)
        total = len(quiz.answers)
        clear_quiz(update, context)
        await query.message.reply_text(
            blank_result_message(correct_count, total, result_lines),
            reply_markup=blank_result_keyboard(quiz.scripture_id, quiz.difficulty or "expert"),
        )
        return

    if data == "blank_undo":
        quiz = get_quiz(update, context)
        if not quiz or quiz.mode != MODE_BLANK:
            await query.edit_message_text(
                "🧩 진행 중인 빈칸 문제가 없습니다.",
                reply_markup=scripture_keyboard(query.message.chat_id),
            )
            return

        if quiz.selected_indexes:
            quiz.selected_indexes.pop()
            set_quiz(update, context, quiz)

        scripture = SCRIPTURE_BY_ID[quiz.scripture_id]
        await query.edit_message_text(
            blank_prompt_text(scripture, quiz),
            reply_markup=blank_choice_keyboard(quiz),
            parse_mode="HTML",
        )
        return

    if data == "blank_submit":
        quiz = get_quiz(update, context)
        if not quiz or quiz.mode != MODE_BLANK:
            await query.edit_message_text(
                "🧩 진행 중인 빈칸 문제가 없습니다.",
                reply_markup=scripture_keyboard(query.message.chat_id),
            )
            return

        if len(quiz.selected_indexes) < len(quiz.answers):
            scripture = SCRIPTURE_BY_ID[quiz.scripture_id]
            await query.edit_message_text(
                blank_prompt_text(scripture, quiz),
                reply_markup=blank_choice_keyboard(quiz),
                parse_mode="HTML",
            )
            return

        await finish_blank_quiz(update, context, quiz)
        return

    if data.startswith("pick:"):
        quiz = get_quiz(update, context)
        if not quiz or quiz.mode != MODE_BLANK:
            await query.edit_message_text(
                "🧩 진행 중인 빈칸 문제가 없습니다.",
                reply_markup=scripture_keyboard(query.message.chat_id),
            )
            return

        option_index = int(data.split(":", 1)[1])
        if option_index not in quiz.selected_indexes:
            quiz.selected_indexes.append(option_index)
            set_quiz(update, context, quiz)

        scripture = SCRIPTURE_BY_ID[quiz.scripture_id]
        await query.edit_message_text(
            blank_prompt_text(scripture, quiz),
            reply_markup=blank_choice_keyboard(quiz),
            parse_mode="HTML",
        )
        return

    if data.startswith("scripture:"):
        scripture_id = data.split(":", 1)[1]
        scripture = SCRIPTURE_BY_ID[scripture_id]
        clear_quiz(update, context)
        if not has_scripture_text(scripture):
            await query.edit_message_text(
                f"📌 {scripture['reference']}\n\n"
                "⚠️ 아직 본문이 입력되지 않았습니다.\n"
                "사용 권한이 있는 본문을 scriptures.py의 text 값에 넣어 주세요.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("📖 다른 성구 선택", callback_data="menu")]]
                ),
            )
            return

        await query.edit_message_text(
            f"📌 {scripture['reference']}\n\n"
            f"{format_scripture_text(scripture['text'], scripture['reference'])}\n\n"
            "어떤 방식으로 연습할까요?",
            reply_markup=mode_keyboard(scripture_id),
        )
        return

    if data.startswith("mode:"):
        _, mode, scripture_id = data.split(":")
        scripture = SCRIPTURE_BY_ID[scripture_id]

        if mode == MODE_FULL:
            quiz = QuizState(
                scripture_id=scripture_id,
                mode=MODE_FULL,
                answers=[scripture["text"]],
            )
            set_quiz(update, context, quiz)
            await query.edit_message_text(
                f"✍️ {scripture['reference']} 전체 암기\n\n"
                "성구 전체를 입력해 주세요.\n"
                "띄어쓰기, 문장부호, 절 번호는 조금 달라도 괜찮습니다.",
                reply_markup=practice_back_keyboard(scripture_id),
            )
            return

        await query.edit_message_text(
            f"🧩 {scripture['reference']} 빈칸 넣기\n\n"
            "난이도를 선택하세요.\n\n"
            "🟢 하: 적은 빈칸\n"
            "🟡 중: 적당한 빈칸\n"
            "🔴 상: 많은 빈칸\n"
            "⚫ 최상: 직접 입력",
            reply_markup=difficulty_keyboard(scripture_id),
        )
        return

    if data.startswith("blank:"):
        _, difficulty, scripture_id = data.split(":")
        scripture = SCRIPTURE_BY_ID[scripture_id]
        quiz_source = format_scripture_text(scripture["text"], scripture["reference"])
        quiz_text, answers, quiz_words, blank_indexes, blank_suffixes = make_blank_quiz(quiz_source, difficulty)
        quiz = QuizState(
            scripture_id=scripture_id,
            mode=MODE_BLANK,
            answers=answers,
            difficulty=difficulty,
            quiz_text=quiz_text,
            quiz_words=quiz_words,
            blank_indexes=blank_indexes,
            blank_suffixes=blank_suffixes,
            options=make_blank_options(answers),
            prompt_message_id=query.message.message_id,
        )
        set_quiz(update, context, quiz)

        if DIFFICULTIES[difficulty].get("subjective"):
            await query.edit_message_text(
                blank_prompt_text(scripture, quiz),
                reply_markup=blank_choice_keyboard(quiz),
                parse_mode="HTML",
            )
        else:
            await query.edit_message_text(
                blank_prompt_text(scripture, quiz),
                reply_markup=blank_choice_keyboard(quiz),
                parse_mode="HTML",
            )


async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    record_visit(update)
    quiz = get_quiz(update, context)
    if not quiz:
        await update.message.reply_text("📖 먼저 /start 로 성구를 선택해 주세요.")
        return

    scripture = SCRIPTURE_BY_ID[quiz.scripture_id]
    submitted = update.message.text

    if quiz.mode == MODE_BLANK:
        if DIFFICULTIES.get(quiz.difficulty or "easy", {}).get("subjective"):
            if len(quiz.typed_answers) < len(quiz.answers):
                quiz.typed_answers.append(submitted.strip())
                set_quiz(update, context, quiz)

            scripture = SCRIPTURE_BY_ID[quiz.scripture_id]
            if quiz.prompt_message_id:
                try:
                    await context.bot.edit_message_text(
                        chat_id=update.effective_chat.id,
                        message_id=quiz.prompt_message_id,
                        text=blank_prompt_text(scripture, quiz),
                        reply_markup=blank_choice_keyboard(quiz),
                        parse_mode="HTML",
                    )
                except TelegramError as error:
                    if is_ignorable_telegram_error(error):
                        return
                    sent_message = await update.message.reply_text(
                        blank_prompt_text(scripture, quiz),
                        reply_markup=blank_choice_keyboard(quiz),
                        parse_mode="HTML",
                    )
                    quiz.prompt_message_id = sent_message.message_id
                    set_quiz(update, context, quiz)
            else:
                sent_message = await update.message.reply_text(
                    blank_prompt_text(scripture, quiz),
                    reply_markup=blank_choice_keyboard(quiz),
                    parse_mode="HTML",
                )
                quiz.prompt_message_id = sent_message.message_id
                set_quiz(update, context, quiz)
            return

        await update.message.reply_text(
            "🧩 빈칸 넣기는 아래 보기 버튼을 눌러 답을 선택해 주세요.",
            reply_markup=blank_choice_keyboard(quiz),
        )
        return

    score, is_exact = score_answer(quiz.answers[0], submitted)
    if is_exact:
        message = "🎉 정확합니다. 완벽하게 암송했어요."
    elif score >= 85:
        message = "👍 거의 다 맞았습니다. 몇 단어만 다시 확인해 보세요."
    elif score >= 60:
        message = "🌊 흐름은 잡았어요. 원문을 한 번 더 읽고 다시 도전해 보세요."
    else:
        message = "🌱 아직 차이가 큽니다. 짧게 끊어서 다시 외워 보세요."

    clear_quiz(update, context)
    memory_diff = build_memory_diff(scripture["text"], submitted)
    await update.message.reply_text(
        f"{message}\n\n"
        f"📊 점수: {score}점\n\n"
        f"🔎 틀린 부분 표시:\n{memory_diff}\n\n"
        f"📖 정답:\n{html.escape(format_scripture_text(scripture['text'], scripture['reference']))}",
        reply_markup=full_result_keyboard(quiz.scripture_id),
        parse_mode="HTML",
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.error:
        if is_ignorable_telegram_error(context.error):
            return
        log_bot_error(update, context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ 처리 중 문제가 발생했습니다. /start 로 다시 시작해 주세요."
            )
        except TelegramError:
            pass


def main() -> None:
    load_dotenv()
    init_database()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError(".env 파일에 TELEGRAM_BOT_TOKEN을 설정해 주세요.")

    if sys.version_info >= (3, 14):
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("remind_on", remind_on))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(handle_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_answer))
    app.add_error_handler(error_handler)

    cleanup_old_records()
    schedule_saved_reminders(app)
    print("Bible memory bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
