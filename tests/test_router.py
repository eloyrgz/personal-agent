import asyncio
import unittest
from unittest.mock import patch

import app


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "error"):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class FakeAsyncClient:
    responses: list[FakeResponse] = []
    calls = 0

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, *args, **kwargs):
        type(self).calls += 1
        return type(self).responses.pop(0)


class RouterTests(unittest.TestCase):
    def test_route_detection_prefers_training_before_home(self):
        self.assertEqual(app.detect_route("Apaga la luz después del entrenamiento"), "training")
        self.assertEqual(app.detect_route("enciende la luz del salón"), "home")
        self.assertEqual(app.detect_route("cuéntame un dato curioso"), "general")

    def test_call_openai_retries_transient_status(self):
        FakeAsyncClient.responses = [
            FakeResponse(429),
            FakeResponse(200, {"choices": [{"message": {"content": "ok"}}]}),
        ]
        FakeAsyncClient.calls = 0

        with (
            patch.object(app, "OPENAI_API_KEY", "test-key"),
            patch.object(app.httpx, "AsyncClient", FakeAsyncClient),
            patch.object(app.asyncio, "sleep", new=_no_sleep),
        ):
            result = asyncio.run(app.call_openai([{"role": "user", "content": "hello"}]))

        self.assertEqual(result, "ok")
        self.assertEqual(FakeAsyncClient.calls, 2)

    def test_call_openai_does_not_retry_client_errors(self):
        FakeAsyncClient.responses = [FakeResponse(400, text="bad request")]
        FakeAsyncClient.calls = 0

        with (
            patch.object(app, "OPENAI_API_KEY", "test-key"),
            patch.object(app.httpx, "AsyncClient", FakeAsyncClient),
            patch.object(app.asyncio, "sleep", new=_no_sleep),
        ):
            with self.assertRaises(app.HTTPException) as error:
                asyncio.run(app.call_openai([{"role": "user", "content": "hello"}]))

        self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(FakeAsyncClient.calls, 1)


async def _no_sleep(_seconds):
    return None


if __name__ == "__main__":
    unittest.main()