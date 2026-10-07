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
        effective_chat=SimpleNamespace(id=user_id),
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
            patch.object(telegram_bot.db_memory, "get_pending_input", return_value=None),
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

    def test_rpe_keyboard_contains_values_one_through_ten(self):
        keyboard = telegram_bot.build_rpe_keyboard("i123")

        self.assertEqual(
            [button.text for row in keyboard.inline_keyboard for button in row],
            [str(value) for value in range(1, 11)],
        )
        self.assertEqual(
            keyboard.inline_keyboard[0][0].callback_data,
            "rpe:i123:1",
        )

    def test_rpe_callback_saves_for_exact_activity(self):
        query = SimpleNamespace(
            data="rpe:i123:7",
            from_user=SimpleNamespace(id=123),
            message=SimpleNamespace(chat_id=123, text="Workout summary", caption=None),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)

        with (
            patch.object(telegram_bot, "ALLOWED_USER_IDS", {123}),
            patch.object(
                telegram_bot,
                "save_activity_rpe",
                return_value={"local_db_updated": True, "intervals_icu_updated": True},
            ) as save_rpe,
        ):
            asyncio.run(telegram_bot.handle_rpe_callback(update, SimpleNamespace()))

        save_rpe.assert_called_once_with("i123", 7)
        query.answer.assert_awaited_once_with()
        query.edit_message_text.assert_awaited_once_with(
            "Workout summary\n\nRPE 7 guardado.",
            reply_markup=telegram_bot.build_note_keyboard("i123"),
        )

    def test_rpe_callback_denies_unlisted_user(self):
        query = SimpleNamespace(
            data="rpe:i123:7",
            from_user=SimpleNamespace(id=456),
            message=SimpleNamespace(chat_id=456, text="Workout summary", caption=None),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)

        with (
            patch.object(telegram_bot, "ALLOWED_USER_IDS", {123}),
            patch.object(telegram_bot, "save_activity_rpe") as save_rpe,
        ):
            asyncio.run(telegram_bot.handle_rpe_callback(update, SimpleNamespace()))

        query.answer.assert_awaited_once_with(
            "⛔ No tienes acceso a esta acción.", show_alert=True
        )
        save_rpe.assert_not_called()

    def test_note_callback_sets_expiring_pending_input(self):
        query = SimpleNamespace(
            data="note:i123",
            from_user=SimpleNamespace(id=123),
            message=SimpleNamespace(chat_id=123, reply_text=AsyncMock()),
            answer=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)

        with (
            patch.object(telegram_bot, "ALLOWED_USER_IDS", {123}),
            patch.object(telegram_bot.db_memory, "set_pending_input", return_value=True) as set_pending,
        ):
            asyncio.run(telegram_bot.handle_note_callback(update, SimpleNamespace()))

        args = set_pending.call_args.args
        self.assertEqual(args[:2], (123, "i123"))
        self.assertGreater(args[2].timestamp(), 0)
        query.message.reply_text.assert_awaited_once()

    def test_pending_note_is_saved_without_calling_agent(self):
        update = make_update(user_id=123, text="Legs felt heavy")

        with (
            patch.object(telegram_bot, "ALLOWED_USER_IDS", {123}),
            patch.object(
                telegram_bot.db_memory,
                "get_pending_input",
                return_value={"activity_id": "i123", "expecting": "note"},
            ),
            patch.object(telegram_bot.db_memory, "clear_pending_input", return_value=True) as clear_pending,
            patch.object(
                telegram_bot,
                "save_activity_note",
                return_value={"local_saved": True, "intervals_icu_updated": True},
            ) as save_note,
            patch.object(telegram_bot, "run_agent") as run_agent,
        ):
            asyncio.run(telegram_bot.handle_message(update, SimpleNamespace()))

        save_note.assert_called_once_with("i123", "Legs felt heavy")
        clear_pending.assert_called_once_with(123)
        run_agent.assert_not_called()
        update.message.reply_text.assert_awaited_once_with("✅ Nota guardada.")


if __name__ == "__main__":
    unittest.main()