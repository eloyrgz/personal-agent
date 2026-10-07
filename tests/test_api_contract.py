import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx


TRAINING_COACH_DIR = Path(__file__).resolve().parents[1] / "training-coach-agent"
sys.path.insert(0, str(TRAINING_COACH_DIR))

import api


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


if __name__ == "__main__":
    unittest.main()