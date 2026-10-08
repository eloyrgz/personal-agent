import asyncio
import os
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import httpx


TRAINING_COACH_DIR = Path(__file__).resolve().parents[1] / "training-coach-agent"
sys.path.insert(0, str(TRAINING_COACH_DIR))

import api
import weekly_recap_runner


class ApiContractTests(unittest.TestCase):
    def setUp(self):
        api._conversations.clear()

    def test_bearer_auth_is_read_from_authorization_header(self):
        async def exercise_requests():
            transport = httpx.ASGITransport(app=api.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                missing = await client.post("/chat", json={"message": "hi", "conversation_id": "test"})
                valid = await client.post(
                    "/chat",
                    headers={"Authorization": "Bearer secret"},
                    json={"message": "hi", "conversation_id": "test"},
                )
            return missing, valid

        with patch.object(api, "API_KEY", "secret"), patch.object(api, "run_agent", return_value="hello"):
            missing, valid = asyncio.run(exercise_requests())

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(valid.status_code, 200)
        self.assertEqual(valid.json()["reply"], "hello")

    def test_reset_requires_the_same_bearer_header(self):
        async def exercise_request():
            transport = httpx.ASGITransport(app=api.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    "/chat/reset?conversation_id=test",
                    headers={"Authorization": "Bearer secret"},
                )

        with patch.object(api, "API_KEY", "secret"):
            response = asyncio.run(exercise_request())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "cleared")

    def test_notification_worker_requires_exactly_one_allowed_chat(self):
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_IDS": "123"}):
            self.assertEqual(api._notification_chat_id(), 123)
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_IDS": "123,456"}):
            self.assertIsNone(api._notification_chat_id())
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_IDS": ""}):
            self.assertIsNone(api._notification_chat_id())

    def test_weekly_recap_auth_generates_and_sends_completed_week(self):
        async def exercise_request():
            transport = httpx.ASGITransport(app=api.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    "/weekly-recap",
                    headers={"Authorization": "Bearer secret"},
                    json={"week_start": "2026-09-28"},
                )

        with (
            patch.object(api, "API_KEY", "secret"),
            patch.object(api.db_memory, "claim_weekly_recap", return_value=True),
            patch.object(api.db_memory, "set_weekly_recap_content") as save_content,
            patch.object(api.db_memory, "complete_weekly_recap", return_value=True),
            patch.object(api.db_memory, "fail_weekly_recap") as fail_recap,
            patch.object(api, "run_weekly_recap_agent", return_value="Semana sólida.") as generate,
            patch.object(api, "_send_weekly_recap_email", return_value="email-123") as send_email,
        ):
            response = asyncio.run(exercise_request())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "sent")
        self.assertEqual(response.json()["week_end"], "2026-10-04")
        generate.assert_called_once_with("2026-09-28", "2026-10-04")
        save_content.assert_called_once()
        send_email.assert_called_once()
        fail_recap.assert_not_called()

    def test_weekly_recap_does_not_regenerate_a_processed_week(self):
        async def exercise_request():
            transport = httpx.ASGITransport(app=api.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    "/weekly-recap",
                    headers={"Authorization": "Bearer secret"},
                    json={"week_start": "2026-09-28"},
                )

        with (
            patch.object(api, "API_KEY", "secret"),
            patch.object(api.db_memory, "claim_weekly_recap", return_value=False),
            patch.object(api, "run_weekly_recap_agent") as generate,
            patch.object(api, "_send_weekly_recap_email") as send_email,
        ):
            response = asyncio.run(exercise_request())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "already_sent_or_processing")
        generate.assert_not_called()
        send_email.assert_not_called()

    def test_weekly_recap_requires_a_monday_start(self):
        async def exercise_request():
            transport = httpx.ASGITransport(app=api.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    "/weekly-recap",
                    headers={"Authorization": "Bearer secret"},
                    json={"week_start": "2026-09-27"},
                )

        with patch.object(api, "API_KEY", "secret"):
            response = asyncio.run(exercise_request())

        self.assertEqual(response.status_code, 422)

    def test_weekly_recap_runner_targets_previous_monday(self):
        self.assertEqual(
            weekly_recap_runner.previous_week_start(date.fromisoformat("2026-10-08")),
            date.fromisoformat("2026-09-28"),
        )

    def test_weekly_recap_email_uses_resend_idempotency_and_escaped_html(self):
        resend_response = unittest.mock.Mock()
        resend_response.is_error = False
        resend_response.json.return_value = {"id": "email-123"}

        with (
            patch.dict(os.environ, {
                "RESEND_API_KEY": "test-key",
                "WEEKLY_RECAP_EMAIL_FROM": "Coach <coach@example.com>",
                "WEEKLY_RECAP_EMAIL_TO": "athlete@example.com",
            }),
            patch.object(api.httpx, "post", return_value=resend_response) as resend_post,
        ):
            message_id = api._send_weekly_recap_email(
                date.fromisoformat("2026-09-28"),
                date.fromisoformat("2026-10-04"),
                "<strong>Semana</strong>",
            )

        self.assertEqual(message_id, "email-123")
        request = resend_post.call_args.kwargs
        self.assertEqual(request["headers"]["Idempotency-Key"], "weekly-recap-2026-09-28")
        self.assertIn("&lt;strong&gt;Semana&lt;/strong&gt;", request["json"]["html"])


if __name__ == "__main__":
    unittest.main()