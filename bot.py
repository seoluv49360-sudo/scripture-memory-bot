import asyncio
import difflib
import json
import os
import random
import re
import sys
from dataclasses import dataclass, field
from datetime import time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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
try:
    KST = ZoneInfo("Asia/Seoul")
except Exception:
    KST = timezone(timedelta(hours=9), name="KST")

REMINDER_TIME = time(hour=8, minute=0, tzinfo=KST)

DIFFICULTIES = {
    "easy": {"label": "하", "ratio": 0.12, "max_blanks": 4, "hint": "가볍게 확인"},
    "medium": {"label": "중", "ratio": 0.2, "max_blanks": 7, "hint": "암송 점검"},
    "hard": {"label": "상", "ratio": 0.3, "max_blanks": 10, "hint": "실전 훈련"},
}

NUMBER_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


@dataclass
class QuizState:
    scripture_id: str
    mode: str
    answers: list[str]
    difficulty: str | None = None
    quiz_text: str = ""
    options: list[str] = field(default_factory=list)
    selected_indexes: list[int] = field(default_factory=list)


def normalize(text: str) -> str:
    compact = re.sub(r"\s+", "", text.strip())
    return re.sub(r"[^\w가-힣]", "", compact).lower()


def remove_verse_numbers(text: str) -> str:
    without_line_numbers = re.sub(r"(?m)^\s*\d{1,3}\s+", "", text)
    return re.sub(r"(?<=\s)\d{1,3}\s+", "", without_line_numbers)


def normalize_for_memory(text: str) -> str:
    without_numbers = remove_verse_numbers(text)
    compact = re.sub(r"\s+", "", without_numbers.strip())
    return re.sub(r"[^A-Za-z가-힣]", "", compact).lower()


def score_answer(expected: str, submitted: str) -> tuple[int, bool]:
    expected_norm = normalize_for_memory(expected)
    submitted_norm = normalize_for_memory(submitted)
    if not expected_norm:
        return 0, False

    ratio = difflib.SequenceMatcher(None, expected_norm, submitted_norm).ratio()
    score = round(ratio * 100)
    return score, expected_norm == submitted_norm


def has_scripture_text(scripture: dict[str, str]) -> bool:
    return scripture["text"].strip() != SCRIPTURE_PLACEHOLDER


def short_reference(reference: str) -> str:
    return reference.replace("요한계시록", "계")


def format_scripture_text(text: str) -> str:
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


def load_reminder_chats() -> set[int]:
    if not REMINDER_FILE.exists():
        return set()
    try:
        data = json.loads(REMINDER_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    return {int(chat_id) for chat_id in data.get("chat_ids", [])}


def save_reminder_chats(chat_ids: set[int]) -> None:
    REMINDER_FILE.write_text(
        json.dumps({"chat_ids": sorted(chat_ids)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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
            f"{format_scripture_text(scripture['text'])}\n\n"
            "✍️ 조용히 한 번 읽고, 눈을 감고 다시 떠올려 보세요."
        ),
    )


def mode_keyboard(scripture_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✍️ 전체 암기 시작", callback_data=f"mode:{MODE_FULL}:{scripture_id}")],
            [InlineKeyboardButton("🧩 빈칸 넣기", callback_data=f"mode:{MODE_BLANK}:{scripture_id}")],
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
    available_buttons = []
    selected = set(quiz.selected_indexes)
    for index, option in enumerate(quiz.options):
        if index not in selected:
            available_buttons.append(InlineKeyboardButton(f"🔹 {option}", callback_data=f"pick:{index}"))

    rows = [available_buttons[index : index + 2] for index in range(0, len(available_buttons), 2)]
    rows.append([InlineKeyboardButton("↩️ 선택 초기화", callback_data="blank_reset")])
    rows.append([InlineKeyboardButton("🎚️ 난이도 변경", callback_data=f"mode:{MODE_BLANK}:{quiz.scripture_id}")])
    return InlineKeyboardMarkup(rows)


def blank_prompt_text(scripture: dict[str, str], quiz: QuizState) -> str:
    difficulty = DIFFICULTIES[quiz.difficulty or "easy"]
    next_number = len(quiz.selected_indexes) + 1
    total = len(quiz.answers)
    selected_answers = [quiz.options[index] for index in quiz.selected_indexes]
    selected_text = "\n".join(
        f"{index}. {answer}" for index, answer in enumerate(selected_answers, start=1)
    )
    if not selected_text:
        selected_text = "아직 선택한 답이 없습니다."

    return (
        f"🧩 {scripture['reference']} 빈칸 넣기\n"
        f"🎚️ 난이도: {difficulty['label']} · {difficulty['hint']}\n\n"
        f"{quiz.quiz_text}\n\n"
        f"👉 지금은 {next_number}번 빈칸 차례입니다.\n"
        f"📍 진행: {len(quiz.selected_indexes)}/{total}\n\n"
        f"✅ 선택한 답:\n{selected_text}"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    await update.message.reply_text(
        "📖 암송할 성구를 선택하세요.\n\n원하는 구절을 누르면 연습 방식을 고를 수 있습니다.",
        reply_markup=scripture_keyboard(update.effective_chat.id),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📖 /start - 성구 선택\n"
        "🔔 /remind_on - 매일 오전 8시 랜덤 성구 받기\n"
        "🛑 /cancel - 현재 문제 취소\n\n"
        "✍️ 전체 암기는 입력한 문장을 원문과 비교해 점수를 보여 줍니다.\n"
        "🧩 빈칸 넣기는 보기 버튼을 빈칸 순서대로 누르면 자동으로 채점됩니다."
    )


async def remind_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    context.user_data.pop("quiz", None)
    await update.message.reply_text(
        "🛑 현재 문제를 취소했습니다.\n\n다시 성구를 골라 주세요.",
        reply_markup=scripture_keyboard(update.effective_chat.id),
    )


def make_blank_quiz(text: str, difficulty: str) -> tuple[str, list[str]]:
    words = text.split()
    difficulty_info = DIFFICULTIES[difficulty]
    if len(words) < 4:
        blank_count = 1
    else:
        ratio_count = max(1, round(len(words) * difficulty_info["ratio"]))
        blank_count = min(ratio_count, difficulty_info["max_blanks"])

    candidate_indexes = [index for index, word in enumerate(words) if len(normalize(word)) >= 2]
    blank_indexes = sorted(random.sample(candidate_indexes, min(blank_count, len(candidate_indexes))))

    answers = []
    quiz_words = words[:]
    for index in blank_indexes:
        answers.append(words[index])
        quiz_words[index] = f"({len(answers)}) {'_' * min(max(len(words[index]), 2), 8)}"

    return " ".join(quiz_words), answers


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


async def finish_blank_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE, quiz: QuizState) -> None:
    query = update.callback_query
    correct_count, result_lines = build_blank_result(quiz)
    total = len(quiz.answers)
    percent = round(correct_count / total * 100) if total else 0
    if percent == 100:
        message = "🎉 모든 빈칸을 맞혔습니다."
    elif percent >= 70:
        message = "👍 좋습니다. 틀린 부분만 다시 보면 금방 잡힙니다."
    else:
        message = "🌱 괜찮아요. 같은 난이도로 한 번 더 풀어 보세요."

    context.user_data.pop("quiz", None)
    await query.edit_message_text(
        f"{message}\n\n"
        f"📊 결과: {correct_count}/{total} ({percent}점)\n\n"
        + "\n".join(result_lines),
        reply_markup=blank_result_keyboard(quiz.scripture_id, quiz.difficulty or "easy"),
    )


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data
    if data == "menu":
        context.user_data.pop("quiz", None)
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
        quiz = context.user_data.get("quiz")
        if not quiz or quiz.mode != MODE_BLANK:
            await query.edit_message_text(
                "🧩 진행 중인 빈칸 문제가 없습니다.",
                reply_markup=scripture_keyboard(query.message.chat_id),
            )
            return

        quiz.selected_indexes.clear()
        scripture = SCRIPTURE_BY_ID[quiz.scripture_id]
        await query.edit_message_text(
            blank_prompt_text(scripture, quiz),
            reply_markup=blank_choice_keyboard(quiz),
        )
        return

    if data.startswith("pick:"):
        quiz = context.user_data.get("quiz")
        if not quiz or quiz.mode != MODE_BLANK:
            await query.edit_message_text(
                "🧩 진행 중인 빈칸 문제가 없습니다.",
                reply_markup=scripture_keyboard(query.message.chat_id),
            )
            return

        option_index = int(data.split(":", 1)[1])
        if option_index not in quiz.selected_indexes:
            quiz.selected_indexes.append(option_index)

        if len(quiz.selected_indexes) >= len(quiz.answers):
            await finish_blank_quiz(update, context, quiz)
            return

        scripture = SCRIPTURE_BY_ID[quiz.scripture_id]
        await query.edit_message_text(
            blank_prompt_text(scripture, quiz),
            reply_markup=blank_choice_keyboard(quiz),
        )
        return

    if data.startswith("scripture:"):
        scripture_id = data.split(":", 1)[1]
        scripture = SCRIPTURE_BY_ID[scripture_id]
        context.user_data.pop("quiz", None)
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
            f"{format_scripture_text(scripture['text'])}\n\n"
            "어떤 방식으로 연습할까요?",
            reply_markup=mode_keyboard(scripture_id),
        )
        return

    if data.startswith("mode:"):
        _, mode, scripture_id = data.split(":")
        scripture = SCRIPTURE_BY_ID[scripture_id]

        if mode == MODE_FULL:
            context.user_data["quiz"] = QuizState(
                scripture_id=scripture_id,
                mode=MODE_FULL,
                answers=[scripture["text"]],
            )
            await query.edit_message_text(
                f"✍️ {scripture['reference']} 전체 암기\n\n"
                "성구 전체를 입력해 주세요.\n"
                "띄어쓰기와 문장부호는 조금 달라도 괜찮습니다."
            )
            return

        await query.edit_message_text(
            f"🧩 {scripture['reference']} 빈칸 넣기\n\n"
            "난이도를 선택하세요.\n\n"
            "🟢 하: 적은 빈칸\n"
            "🟡 중: 적당한 빈칸\n"
            "🔴 상: 많은 빈칸",
            reply_markup=difficulty_keyboard(scripture_id),
        )
        return

    if data.startswith("blank:"):
        _, difficulty, scripture_id = data.split(":")
        scripture = SCRIPTURE_BY_ID[scripture_id]
        quiz_text, answers = make_blank_quiz(scripture["text"], difficulty)
        quiz = QuizState(
            scripture_id=scripture_id,
            mode=MODE_BLANK,
            answers=answers,
            difficulty=difficulty,
            quiz_text=quiz_text,
            options=make_blank_options(answers),
        )
        context.user_data["quiz"] = quiz

        await query.edit_message_text(
            blank_prompt_text(scripture, quiz),
            reply_markup=blank_choice_keyboard(quiz),
        )


async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    quiz = context.user_data.get("quiz")
    if not quiz:
        await update.message.reply_text("📖 먼저 /start 로 성구를 선택해 주세요.")
        return

    scripture = SCRIPTURE_BY_ID[quiz.scripture_id]
    submitted = update.message.text

    if quiz.mode == MODE_BLANK:
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

    context.user_data.pop("quiz", None)
    await update.message.reply_text(
        f"{message}\n\n"
        f"📊 점수: {score}점\n\n"
        f"📖 정답:\n{format_scripture_text(scripture['text'])}",
        reply_markup=full_result_keyboard(quiz.scripture_id),
    )


def main() -> None:
    load_dotenv()
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
    app.add_handler(CommandHandler("remind_on", remind_on))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(handle_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_answer))

    schedule_saved_reminders(app)
    print("Bible memory bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
