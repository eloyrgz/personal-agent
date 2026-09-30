import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx


BRIDGE_DIR = Path(__file__).resolve().parents[1] / "training-coach-bridge"
sys.path.insert(0, str(BRIDGE_DIR))

import bridge

REAL_ASYNC_CLIENT = httpx.AsyncClient


class FakeResponse:
    status_code = 200
    text = ""

    def json(self):
        return {"reply": "Training response"}


class FakeAsyncClient:
    payload = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, url, json):
        type(self).payload = {"url": url, "json": json}
        return FakeResponse()


class BridgeTests(unittest.TestCase):
    def test_models_endpoint_exposes_training_coach(self):
        async def exercise_request():
            transport = httpx.ASGITransport(app=bridge.app)
            async with REAL_ASYNC_CLIENT(transport=transport, base_url="http://test") as client:
                return await client.get("/v1/models")

        response = asyncio.run(exercise_request())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"][0]["id"], "training-coach")

    def test_completion_forwards_stable_conversation_id(self):
        FakeAsyncClient.payload = None
        request = {"messages": [{"role": "user", "content": "How did I train?"}]}

        async def exercise_request():
            transport = httpx.ASGITransport(app=bridge.app)
            async with REAL_ASYNC_CLIENT(transport=transport, base_url="http://test") as client:
                return await client.post("/v1/chat/completions", json=request)

        with patch.object(bridge.httpx, "AsyncClient", FakeAsyncClient):
            response = asyncio.run(exercise_request())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["choices"][0]["message"]["content"], "Training response")
        self.assertEqual(FakeAsyncClient.payload["json"]["conversation_id"], "openwebui-96d6b6f213f8e1a2")


if __name__ == "__main__":
    unittest.main()