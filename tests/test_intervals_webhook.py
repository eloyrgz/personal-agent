import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx


TRAINING_COACH_DIR = Path(__file__).resolve().parents[1] / "training-coach-agent"
sys.path.insert(0, str(TRAINING_COACH_DIR))

import intervals_webhook


class IntervalsWebhookTests(unittest.TestCase):
    def setUp(self):
        intervals_webhook._pending_oauth_states.clear()

    def post(self, payload):
        async def request():
            transport = httpx.ASGITransport(app=intervals_webhook.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post("/webhooks/intervals/activity", json=payload)

        return asyncio.run(request())

    def get(self, path):
        async def request():
            transport = httpx.ASGITransport(app=intervals_webhook.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.get(path, follow_redirects=False)

        return asyncio.run(request())

    def test_oauth_start_redirects_with_read_only_scope_and_state(self):
        with patch.dict(os.environ, {"INTERVALS_OAUTH_CLIENT_ID": "client123"}):
            response = self.get("/oauth/start")

        self.assertEqual(response.status_code, 302)
        location = response.headers["location"]
        self.assertIn("scope=ACTIVITY%3AREAD", location)
        self.assertIn("redirect_uri=https%3A%2F%2Fintervals-webhook.err-hass.duckdns.org%2Foauth%2Fcallback", location)
        self.assertIn("state=", location)

    def test_oauth_callback_requires_valid_one_time_state(self):
        response = self.get("/oauth/callback?code=unused&state=forged")

        self.assertEqual(response.status_code, 400)

    def test_oauth_callback_rejects_missing_activity_read_scope(self):
        with patch.dict(os.environ, {"INTERVALS_OAUTH_CLIENT_ID": "client123"}):
            start_response = self.get("/oauth/start")
        from urllib.parse import parse_qs, urlparse
        state = parse_qs(urlparse(start_response.headers["location"]).query)["state"][0]
        token_response = unittest.mock.MagicMock()
        token_response.json.return_value = {
            "access_token": "do-not-display-or-store",
            "scope": "SETTINGS:READ",
            "athlete": {"id": "2049151"},
        }
        with (
            patch.dict(os.environ, {
                "INTERVALS_OAUTH_CLIENT_ID": "client123",
                "INTERVALS_OAUTH_CLIENT_SECRET": "client-secret",
            }),
            patch.object(intervals_webhook.requests, "post", return_value=token_response),
        ):
            response = self.get(f"/oauth/callback?code=one-time-code&state={state}")

        self.assertEqual(response.status_code, 403)
        self.assertNotIn("do-not-display-or-store", response.text)

    def test_oauth_callback_returns_controlled_error_when_exchange_fails(self):
        with patch.dict(os.environ, {"INTERVALS_OAUTH_CLIENT_ID": "client123"}):
            start_response = self.get("/oauth/start")
        from urllib.parse import parse_qs, urlparse
        state = parse_qs(urlparse(start_response.headers["location"]).query)["state"][0]
        with (
            patch.dict(os.environ, {
                "INTERVALS_OAUTH_CLIENT_ID": "client123",
                "INTERVALS_OAUTH_CLIENT_SECRET": "client-secret",
            }),
            patch.object(
                intervals_webhook.requests,
                "post",
                side_effect=intervals_webhook.requests.ConnectionError("offline"),
            ),
        ):
            response = self.get(f"/oauth/callback?code=one-time-code&state={state}")

        self.assertEqual(response.status_code, 502)
        self.assertNotIn("one-time-code", response.text)

    def test_oauth_callback_exchanges_code_and_displays_only_athlete_id(self):
        with patch.dict(os.environ, {"INTERVALS_OAUTH_CLIENT_ID": "client123"}):
            start_response = self.get("/oauth/start")
        from urllib.parse import parse_qs, urlparse
        state = parse_qs(urlparse(start_response.headers["location"]).query)["state"][0]
        token_response = unittest.mock.MagicMock()
        token_response.json.return_value = {
            "access_token": "do-not-display-or-store",
            "scope": "ACTIVITY:READ",
            "athlete": {"id": "2049151"},
        }
        with (
            patch.dict(os.environ, {
                "INTERVALS_OAUTH_CLIENT_ID": "client123",
                "INTERVALS_OAUTH_CLIENT_SECRET": "client-secret",
            }),
            patch.object(intervals_webhook.requests, "post", return_value=token_response) as token_exchange,
        ):
            response = self.get(f"/oauth/callback?code=one-time-code&state={state}")

        self.assertEqual(response.status_code, 200)
        self.assertIn("INTERVALS_WEBHOOK_ATHLETE_ID=2049151", response.text)
        self.assertNotIn("do-not-display-or-store", response.text)
        token_exchange.assert_called_once()
        self.assertEqual(token_exchange.call_args.kwargs["data"]["code"], "one-time-code")
        self.assertFalse(token_exchange.call_args.kwargs["allow_redirects"])

        reused = self.get(f"/oauth/callback?code=one-time-code&state={state}")
        self.assertEqual(reused.status_code, 400)

    def test_rejects_missing_configured_secret(self):
        with patch.object(intervals_webhook, "INTERVALS_WEBHOOK_SECRET", ""):
            response = self.post({"secret": "secret", "events": []})
        self.assertEqual(response.status_code, 503)

    def test_rejects_wrong_secret(self):
        with patch.object(intervals_webhook, "INTERVALS_WEBHOOK_SECRET", "secret"):
            response = self.post({"secret": "wrong", "events": []})
        self.assertEqual(response.status_code, 401)

    def test_queues_only_analyzed_events_for_configured_athlete(self):
        payload = {
            "secret": "secret",
            "events": [
                {
                    "athlete_id": "2049151",
                    "type": "ACTIVITY_ANALYZED",
                    "activity": {"id": "i67890"},
                },
                {
                    "athlete_id": "2049151",
                    "type": "ACTIVITY_UPLOADED",
                    "activity": {"id": "i99999"},
                },
                {
                    "athlete_id": "i-other",
                    "type": "ACTIVITY_ANALYZED",
                    "activity": {"id": "i00000"},
                },
            ],
        }
        with (
            patch.object(intervals_webhook, "INTERVALS_WEBHOOK_SECRET", "secret"),
            patch.dict(os.environ, {
                "INTERVALS_ATHLETE_ID": "i12345",
                "INTERVALS_WEBHOOK_ATHLETE_ID": "2049151",
            }),
            patch.object(intervals_webhook, "_enqueue_activity_ids", return_value=(1, 0)) as enqueue,
        ):
            response = self.post(payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "accepted", "queued": 1, "duplicates": 0, "ignored": 2},
        )
        enqueue.assert_called_once_with(["i67890"])

    def test_counts_duplicates_and_returns_503_on_storage_failure(self):
        payload = {
            "secret": "secret",
            "events": [{
                "athlete_id": "2049151",
                "type": "ACTIVITY_ANALYZED",
                "activity": {"id": "i67890"},
            }],
        }
        with (
            patch.object(intervals_webhook, "INTERVALS_WEBHOOK_SECRET", "secret"),
            patch.dict(os.environ, {
                "INTERVALS_ATHLETE_ID": "i12345",
                "INTERVALS_WEBHOOK_ATHLETE_ID": "2049151",
            }),
            patch.object(intervals_webhook, "_enqueue_activity_ids", side_effect=[(0, 1), RuntimeError]),
        ):
            duplicate = self.post(payload)
            unavailable = self.post(payload)

        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(duplicate.json()["duplicates"], 1)
        self.assertEqual(unavailable.status_code, 503)

    def test_ignores_analyzed_event_without_activity_id(self):
        payload = {
            "secret": "secret",
            "events": [{
                "athlete_id": "2049151",
                "type": "ACTIVITY_ANALYZED",
                "activity": {},
            }],
        }
        with (
            patch.object(intervals_webhook, "INTERVALS_WEBHOOK_SECRET", "secret"),
            patch.dict(os.environ, {"INTERVALS_WEBHOOK_ATHLETE_ID": "2049151"}),
            patch.object(intervals_webhook, "_enqueue_activity_ids") as enqueue,
        ):
            response = self.post(payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ignored"], 1)
        enqueue.assert_not_called()

    def test_batch_enqueue_commits_once_and_counts_duplicates(self):
        connection = MagicMock()
        cursor = MagicMock()
        cursor.fetchone.side_effect = [{"activity_id": "i1"}, None]
        cursor_context = MagicMock()
        cursor_context.__enter__.return_value = cursor
        connection.cursor.return_value = cursor_context

        with (
            patch.dict(os.environ, {"SUPABASE_DB_URI": "postgresql://test"}),
            patch.object(intervals_webhook.psycopg2, "connect", return_value=connection),
        ):
            result = intervals_webhook._enqueue_activity_ids(["i1", "i2"])

        self.assertEqual(result, (1, 1))
        self.assertEqual(cursor.execute.call_count, 2)
        connection.commit.assert_called_once()
        connection.close.assert_called_once()

    def test_batch_enqueue_rolls_back_on_database_error(self):
        connection = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("database unavailable")
        cursor_context = MagicMock()
        cursor_context.__enter__.return_value = cursor
        connection.cursor.return_value = cursor_context

        with (
            patch.dict(os.environ, {"SUPABASE_DB_URI": "postgresql://test"}),
            patch.object(intervals_webhook.psycopg2, "connect", return_value=connection),
        ):
            with self.assertRaises(RuntimeError):
                intervals_webhook._enqueue_activity_ids(["i1"])

        connection.rollback.assert_called_once()
        connection.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()