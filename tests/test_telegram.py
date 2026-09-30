import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


TRAINING_COACH_DIR = Path(__file__).resolve().parents[1] / "training-coach-agent"
sys.path.insert(0, str(TRAINING_COACH_DIR))

import telegram_bot


def make_update(user_id: int = 123, first_name: str = "Eloy", text: str = "hello"):
    message = SimpleNamespace(
        text=text,
        reply_text=AsyncMock(),
        chat=SimpleNamespace(send_action=AsyncMock()),
    )
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id, first_name=first_name),
        message=message,
    )


class TelegramBotTests(unittest.TestCase):
    def setUp(self):
        telegram_bot._conversations.clear()

    def test_denied_user_receives_access_message(self):
        update = make_update(user_id=456)

        with patch.object(telegram_bot, "ALLOWED_USER_IDS", {123}):
            asyncio.run(telegram_bot.start(update, SimpleNamespace()))

        update.message.reply_text.assert_awaited_once_with("⛔ No tienes acceso a este bot.")

    def test_start_resets_conversation_for_allowed_user(self):
        update = make_update(user_id=123)
        telegram_bot._conversations[123] = ["old history"]

        with patch.object(telegram_bot, "ALLOWED_USER_IDS", {123}):
            asyncio.run(telegram_bot.start(update, SimpleNamespace()))

        self.assertEqual(telegram_bot._conversations[123], [])
        update.message.reply_text.assert_awaited_once()
        self.assertIn("Hola Eloy", update.message.reply_text.await_args.args[0])

    def test_message_handler_runs_agent_and_stores_reply(self):
        update = make_update(user_id=123, text="How was my training?")

        with (
            patch.object(telegram_bot, "ALLOWED_USER_IDS", {123}),
            patch.object(telegram_bot, "run_agent", return_value="Training was solid"),
        ):
            asyncio.run(telegram_bot.handle_message(update, SimpleNamespace()))

        self.assertEqual(len(telegram_bot._conversations[123]), 2)
        self.assertEqual(telegram_bot._conversations[123][1].content, "Training was solid")
        update.message.reply_text.assert_awaited_once_with(
            "Training was solid", parse_mode=telegram_bot.ParseMode.MARKDOWN
        )

    def test_split_message_respects_limit(self):
        self.assertEqual(telegram_bot._split_message("abcdef", limit=3), ["abc", "def"])


if __name__ == "__main__":
    unittest.main()