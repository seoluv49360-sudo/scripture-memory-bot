import asyncio
import json
import os
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import bot


class SecurityControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_db_file = bot.DB_FILE
        bot.DB_FILE = Path(self.temp_dir.name) / "test.sqlite3"
        bot.REQUEST_TIMESTAMPS.clear()
        bot.init_database()

    def tearDown(self) -> None:
        bot.DB_FILE = self.original_db_file
        self.temp_dir.cleanup()

    def test_member_access_payload_uses_configured_token(self) -> None:
        token = "a" * 32
        with patch.dict(os.environ, {"MEMBER_ACCESS_TOKEN": token}, clear=False):
            self.assertTrue(bot.member_access_required())
            self.assertTrue(bot.member_access_payload_matches(token))
            self.assertFalse(bot.member_access_payload_matches("wrong-token"))
            self.assertFalse(bot.member_access_payload_matches(""))
            self.assertTrue(bot.is_valid_member_access_token(token))

    def test_member_access_token_can_be_blank(self) -> None:
        with patch.dict(os.environ, {"MEMBER_ACCESS_TOKEN": ""}, clear=False):
            self.assertFalse(bot.member_access_required())
            self.assertFalse(bot.member_access_payload_matches(""))

    def test_bot_token_requires_valid_format_and_rotation_confirmation(self) -> None:
        self.assertTrue(bot.is_valid_telegram_bot_token("123456789:" + "a" * 35))
        self.assertFalse(bot.is_valid_telegram_bot_token("old-or-placeholder-token"))
        with patch.dict(os.environ, {"BOT_TOKEN_ROTATION_CONFIRMED": "true"}, clear=False):
            self.assertTrue(bot.is_bot_token_rotation_confirmed())

    def test_pick_selection_requires_current_message_and_valid_index(self) -> None:
        quiz = bot.QuizState(
            scripture_id="rev_1_1_3",
            mode=bot.MODE_BLANK,
            answers=["answer"],
            options=["answer"],
            prompt_message_id=77,
        )
        self.assertTrue(bot.is_valid_pick_selection(quiz, 0, 77))
        self.assertFalse(bot.is_valid_pick_selection(quiz, 1, 77))
        self.assertFalse(bot.is_valid_pick_selection(quiz, 0, 78))

    def test_blank_quiz_avoids_weak_phrase_starts(self) -> None:
        text = "13 귀 있는 자는 성령이 교회들에게 하시는 말씀을 들을찌어다"
        random.seed(1)
        _, answers, _, _, _ = bot.make_blank_quiz(text, "hard")
        self.assertTrue(answers)
        self.assertFalse(any(answer.startswith("자는 ") for answer in answers))
        self.assertNotIn("하시는", answers)
        self.assertNotIn("easy", bot.DIFFICULTIES)

    def test_approved_member_is_persisted(self) -> None:
        self.assertFalse(bot.is_approved_member(12345))
        bot.approve_member(12345)
        self.assertTrue(bot.is_approved_member(12345))

    def test_database_admin_and_lockout_protection(self) -> None:
        with patch.dict(os.environ, {"ADMIN_USER_IDS": "100"}, clear=False):
            bot.init_database()
            self.assertTrue(bot.is_admin_user(100))
            self.assertTrue(bot.add_administrator(200, 100))
            self.assertTrue(bot.is_admin_user(200))
            removed, _ = bot.remove_administrator(200, 100)
            self.assertTrue(removed)
            removed, _ = bot.remove_administrator(100, 200)
            self.assertFalse(removed)

    def test_request_limit_is_per_user(self) -> None:
        now = datetime.now(timezone.utc)
        with patch.object(bot, "REQUEST_LIMIT_COUNT", 2), patch.object(
            bot, "REQUEST_LIMIT_WINDOW_SECONDS", 10
        ):
            self.assertTrue(bot.request_allowed(1, now))
            self.assertTrue(bot.request_allowed(1, now + timedelta(seconds=1)))
            self.assertFalse(bot.request_allowed(1, now + timedelta(seconds=2)))
            self.assertTrue(bot.request_allowed(2, now + timedelta(seconds=2)))
            self.assertTrue(bot.request_allowed(1, now + timedelta(seconds=11)))

    def test_repeated_auth_failures_are_temporarily_blocked(self) -> None:
        now = datetime.now(timezone.utc)
        with patch.object(bot, "AUTH_FAILURE_LIMIT", 2), patch.object(
            bot, "AUTH_FAILURE_WINDOW_SECONDS", 60
        ), patch.object(bot, "AUTH_BLOCK_SECONDS", 120):
            self.assertFalse(bot.record_auth_failure(700, now))
            self.assertTrue(bot.record_auth_failure(700, now + timedelta(seconds=1)))
            self.assertTrue(bot.auth_failure_is_blocked(700, now + timedelta(seconds=2)))
            bot.clear_auth_failures(700)
            self.assertFalse(bot.auth_failure_is_blocked(700, now + timedelta(seconds=2)))

    def test_private_chat_requirement(self) -> None:
        private_update = bot.Update.de_json(
            {
                "update_id": 12,
                "message": {
                    "message_id": 1,
                    "date": 0,
                    "chat": {"id": 22, "type": "private"},
                    "from": {"id": 11, "is_bot": False, "first_name": "Tester"},
                    "text": "/start",
                },
            },
            None,
        )
        self.assertTrue(asyncio.run(bot.require_private_chat(private_update)))

    def test_safe_update_summary_excludes_message_content(self) -> None:
        update = bot.Update.de_json(
            {
                "update_id": 7,
                "message": {
                    "message_id": 1,
                    "date": 0,
                    "chat": {"id": 22, "type": "private"},
                    "from": {"id": 11, "is_bot": False, "first_name": "Tester"},
                    "text": "private answer text",
                },
            },
            None,
        )
        summary = bot.safe_update_summary(update)
        parsed = json.loads(summary)
        self.assertEqual(parsed, {"update_id": 7, "kind": "message"})
        self.assertNotIn("private answer text", summary)
        self.assertNotIn("Tester", summary)

    def test_safe_error_summary_only_keeps_exception_class(self) -> None:
        error = RuntimeError("C:\\secret\\path private answer text")
        self.assertEqual(bot.safe_error_summary(error), "RuntimeError")

    def test_error_log_stores_only_safe_summaries(self) -> None:
        update = bot.Update.de_json(
            {
                "update_id": 9,
                "message": {
                    "message_id": 2,
                    "date": 0,
                    "chat": {"id": 44, "type": "private"},
                    "from": {"id": 33, "is_bot": False, "first_name": "Private"},
                    "text": "do not store this answer",
                },
            },
            None,
        )
        bot.log_bot_error(update, RuntimeError("C:\\private\\file"))
        with bot.db_connect() as connection:
            payload, error = connection.execute(
                "SELECT update_payload, error FROM bot_errors ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(json.loads(payload), {"update_id": 9, "kind": "message"})
        self.assertEqual(error, "RuntimeError")
        self.assertNotIn("do not store this answer", payload)
        self.assertNotIn("private", error.lower())


if __name__ == "__main__":
    unittest.main()
