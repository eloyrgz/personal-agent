import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


TRAINING_COACH_DIR = Path(__file__).resolve().parents[1] / "training-coach-agent"
sys.path.insert(0, str(TRAINING_COACH_DIR))

from injury_agent import _extract_medical_history_signals, _extract_symptom_signals
from sync_pipeline import TrainingDataPipeline
from agent_memory import SupabaseAgentMemory
import coach_tools
from activity_notifications import ActivityNotificationWorker, format_activity_summary


class InjurySignalTests(unittest.TestCase):
    def test_extracts_severity_swelling_and_red_flags(self):
        result = _extract_symptom_signals(
            "Dolor 7/10, inflamación y hormigueo; cada día empeora y no puedo apoyar"
        )

        self.assertEqual(result["severity_0_10"], 7)
        self.assertTrue(result["swelling"])
        self.assertEqual(
            result["red_flags"],
            ["neurological symptoms", "cannot bear weight", "progressive worsening"],
        )

    def test_extracts_prior_injury_and_surgery_history(self):
        result = _extract_medical_history_signals("Neuroma previo y cirugía del pie")

        self.assertEqual(
            result,
            {
                "has_history": True,
                "relevant_prior_injury": True,
                "recent_surgery": True,
            },
        )


class SyncPipelineHelperTests(unittest.TestCase):
    def test_extract_host_handles_valid_and_invalid_uris(self):
        self.assertEqual(
            TrainingDataPipeline._extract_host("postgresql://user:pass@db.example/postgres"),
            "db.example",
        )
        self.assertIsNone(TrainingDataPipeline._extract_host("not-a-uri"))


class ActivityMemoryTests(unittest.TestCase):
    def test_get_activity_by_id_queries_exact_intervals_id(self):
        memory = SupabaseAgentMemory.__new__(SupabaseAgentMemory)
        cursor = MagicMock()
        cursor.fetchone.return_value = {
            "activity_id": "i123",
            "activity_name": "Morning Run",
        }
        cursor_context = MagicMock()
        cursor_context.__enter__.return_value = cursor
        memory._cursor = MagicMock(return_value=cursor_context)

        result = memory.get_activity_by_id("i123")

        self.assertEqual(result["activity_id"], "i123")
        self.assertEqual(cursor.execute.call_args.args[1], ("i123",))
        self.assertIn("WHERE activity_id = %s", cursor.execute.call_args.args[0])

    def test_get_activity_by_id_returns_none_when_missing(self):
        memory = SupabaseAgentMemory.__new__(SupabaseAgentMemory)
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        cursor_context = MagicMock()
        cursor_context.__enter__.return_value = cursor
        memory._cursor = MagicMock(return_value=cursor_context)

        self.assertIsNone(memory.get_activity_by_id("missing"))

    def test_get_activities_without_events_filters_by_recency_and_rpe(self):
        memory = SupabaseAgentMemory.__new__(SupabaseAgentMemory)
        cursor = MagicMock()
        cursor.fetchall.return_value = [{"activity_id": "i123"}]
        cursor_context = MagicMock()
        cursor_context.__enter__.return_value = cursor
        memory._cursor = MagicMock(return_value=cursor_context)

        self.assertEqual(memory.get_activities_without_events(2), ["i123"])
        self.assertEqual(cursor.execute.call_args.args[1], (2,))
        sql = cursor.execute.call_args.args[0]
        self.assertIn("NOT EXISTS", sql)
        self.assertIn("metric.rpe IS NULL", sql)

    def test_enqueue_activity_event_reports_new_event(self):
        memory = SupabaseAgentMemory.__new__(SupabaseAgentMemory)
        memory.conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchone.return_value = {"activity_id": "i123"}
        cursor_context = MagicMock()
        cursor_context.__enter__.return_value = cursor
        memory._cursor = MagicMock(return_value=cursor_context)

        self.assertTrue(memory.enqueue_activity_event("i123"))
        self.assertEqual(cursor.execute.call_args.args[1], ("i123",))
        memory.conn.commit.assert_called_once()

    def test_enqueue_activity_event_reports_duplicate(self):
        memory = SupabaseAgentMemory.__new__(SupabaseAgentMemory)
        memory.conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        cursor_context = MagicMock()
        cursor_context.__enter__.return_value = cursor
        memory._cursor = MagicMock(return_value=cursor_context)

        self.assertFalse(memory.enqueue_activity_event("i123"))
        memory.conn.commit.assert_called_once()

    def test_enqueue_activity_event_reports_database_error(self):
        memory = SupabaseAgentMemory.__new__(SupabaseAgentMemory)
        memory.conn = MagicMock()
        memory._cursor = MagicMock(side_effect=RuntimeError("database unavailable"))

        self.assertIsNone(memory.enqueue_activity_event("i123"))
        memory.conn.rollback.assert_called_once()

    def test_claim_next_activity_event_uses_skip_locked(self):
        memory = SupabaseAgentMemory.__new__(SupabaseAgentMemory)
        memory.conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchone.return_value = {"activity_id": "i123", "attempts": 1}
        cursor_context = MagicMock()
        cursor_context.__enter__.return_value = cursor
        memory._cursor = MagicMock(return_value=cursor_context)

        event = memory.claim_next_activity_event()

        self.assertEqual(event["activity_id"], "i123")
        self.assertIn("FOR UPDATE SKIP LOCKED", cursor.execute.call_args.args[0])
        memory.conn.commit.assert_called_once()

    def test_complete_activity_event_persists_delivery_details(self):
        memory = SupabaseAgentMemory.__new__(SupabaseAgentMemory)
        memory.conn = MagicMock()
        cursor = MagicMock()
        cursor.rowcount = 1
        cursor_context = MagicMock()
        cursor_context.__enter__.return_value = cursor
        memory._cursor = MagicMock(return_value=cursor_context)

        updated = memory.complete_activity_event(
            "i123", "awaiting_feedback", "summary", 123, 456
        )

        self.assertTrue(updated)
        self.assertEqual(
            cursor.execute.call_args.args[1],
            ("awaiting_feedback", "summary", 123, 456, "i123"),
        )

    def test_complete_activity_event_rejects_unknown_status(self):
        memory = SupabaseAgentMemory.__new__(SupabaseAgentMemory)

        with self.assertRaises(ValueError):
            memory.complete_activity_event("i123", "queued")

    def test_retry_activity_event_schedules_retry(self):
        memory = SupabaseAgentMemory.__new__(SupabaseAgentMemory)
        memory.conn = MagicMock()
        cursor = MagicMock()
        cursor.rowcount = 1
        cursor_context = MagicMock()
        cursor_context.__enter__.return_value = cursor
        memory._cursor = MagicMock(return_value=cursor_context)

        self.assertTrue(memory.retry_activity_event("i123", "temporary error", 30))
        self.assertEqual(cursor.execute.call_args.args[1], (30, "temporary error", "i123"))

    def test_set_pending_input_upserts_by_chat_id(self):
        memory = SupabaseAgentMemory.__new__(SupabaseAgentMemory)
        memory.conn = MagicMock()
        cursor = MagicMock()
        cursor_context = MagicMock()
        cursor_context.__enter__.return_value = cursor
        memory._cursor = MagicMock(return_value=cursor_context)

        self.assertTrue(memory.set_pending_input(123, "i123", "2030-01-01T00:30:00Z"))
        self.assertEqual(cursor.execute.call_args.args[1], (123, "i123", "2030-01-01T00:30:00Z"))
        self.assertIn("ON CONFLICT (chat_id) DO UPDATE", cursor.execute.call_args.args[0])

    def test_get_pending_input_filters_expired_records(self):
        memory = SupabaseAgentMemory.__new__(SupabaseAgentMemory)
        memory.conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchone.return_value = {"activity_id": "i123", "expecting": "note"}
        cursor_context = MagicMock()
        cursor_context.__enter__.return_value = cursor
        memory._cursor = MagicMock(return_value=cursor_context)

        result = memory.get_pending_input(123)

        self.assertEqual(result["activity_id"], "i123")
        self.assertEqual(cursor.execute.call_count, 2)
        self.assertIn("expires_at <= now()", cursor.execute.call_args_list[0].args[0])
        self.assertIn("expires_at > now()", cursor.execute.call_args_list[1].args[0])

    def test_save_activity_note_updates_one_notification_event(self):
        memory = SupabaseAgentMemory.__new__(SupabaseAgentMemory)
        memory.conn = MagicMock()
        cursor = MagicMock()
        cursor.rowcount = 1
        cursor_context = MagicMock()
        cursor_context.__enter__.return_value = cursor
        memory._cursor = MagicMock(return_value=cursor_context)

        self.assertTrue(memory.save_activity_note("i123", "Felt strong"))
        self.assertEqual(cursor.execute.call_args.args[1], ("Felt strong", "i123"))
        self.assertIn("WHERE activity_id = %s", cursor.execute.call_args.args[0])


class ActivityRpeTests(unittest.TestCase):
    def test_save_activity_rpe_updates_exact_activity(self):
        client = MagicMock()
        activity = {"activity_id": "i123", "activity_name": "Morning Run"}
        with (
            patch.object(coach_tools.memory, "get_activity_by_id", return_value=activity),
            patch.object(coach_tools.memory, "update_rpe_local", return_value=True) as update_local,
        ):
            result = coach_tools.save_activity_rpe("i123", 7, client)

        client.update_activity.assert_called_once_with("i123", {"icu_rpe": 7})
        update_local.assert_called_once_with("i123", 7)
        self.assertTrue(result["intervals_icu_updated"])
        self.assertTrue(result["local_db_updated"])
        self.assertEqual(result["activity"], "Morning Run")


class ActivityNotificationWorkerTests(unittest.TestCase):
    def test_summary_includes_available_activity_metrics(self):
        summary = format_activity_summary({
            "activity_name": "Morning Run",
            "activity_type": "Run",
            "activity_date": "2026-10-04T07:15:00",
            "distance_meters": 10250,
            "moving_time_seconds": 3660,
            "elevation_gain_meters": 125,
            "icu_load": 72,
        })

        self.assertIn("Morning Run", summary)
        self.assertIn("10.25 km", summary)
        self.assertIn("1 h 1 min", summary)
        self.assertIn("Carga: 72", summary)

    def test_worker_syncs_and_enqueues_recent_activities(self):
        memory = MagicMock()
        memory.get_activities_without_events.return_value = ["i123", "i456"]
        sync_recent = MagicMock()
        worker = ActivityNotificationWorker(memory, MagicMock(), 123, sync_recent)

        asyncio.run(worker.discover_recent_activities())

        sync_recent.assert_called_once_with()
        self.assertEqual(memory.enqueue_activity_event.call_count, 2)
        memory.enqueue_activity_event.assert_any_call("i123")
        memory.enqueue_activity_event.assert_any_call("i456")

    def test_worker_sends_keyboard_and_persists_message(self):
        memory = MagicMock()
        memory.claim_next_activity_event.return_value = {"activity_id": "i123", "attempts": 1}
        memory.get_activity_by_id.return_value = {
            "activity_id": "i123",
            "activity_name": "Morning Run",
            "moving_time_seconds": 3600,
        }
        memory.complete_activity_event.return_value = True
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MagicMock(message_id=456))
        worker = ActivityNotificationWorker(memory, bot, 123)

        self.assertTrue(asyncio.run(worker.process_one()))

        bot.send_message.assert_awaited_once()
        self.assertEqual(bot.send_message.call_args.kwargs["chat_id"], 123)
        completed_args = memory.complete_activity_event.call_args.args
        self.assertEqual(completed_args[:2], ("i123", "awaiting_feedback"))
        self.assertIn("Morning Run", completed_args[2])
        self.assertIn("1 h 0 min", completed_args[2])
        self.assertEqual(completed_args[3:], (123, 456))

    def test_worker_marks_short_activity_done_without_sending(self):
        memory = MagicMock()
        memory.claim_next_activity_event.return_value = {"activity_id": "i123", "attempts": 1}
        memory.get_activity_by_id.return_value = {"moving_time_seconds": 299}
        memory.complete_activity_event.return_value = True
        bot = MagicMock()
        worker = ActivityNotificationWorker(memory, bot, 123)

        self.assertTrue(asyncio.run(worker.process_one()))

        memory.complete_activity_event.assert_called_once_with("i123", "done")
        bot.send_message.assert_not_called()

    def test_save_activity_rpe_rejects_unknown_activity(self):
        with patch.object(coach_tools.memory, "get_activity_by_id", return_value=None):
            result = coach_tools.save_activity_rpe("missing", 7, MagicMock())

        self.assertIn("error", result)

    def test_save_activity_rpe_rejects_invalid_value(self):
        with patch.object(coach_tools.memory, "get_activity_by_id") as get_activity:
            result = coach_tools.save_activity_rpe("i123", 11, MagicMock())

        self.assertIn("error", result)
        get_activity.assert_not_called()

    def test_save_activity_note_writes_to_exact_intervals_activity(self):
        client = MagicMock()
        with patch.object(coach_tools.memory, "save_activity_note", return_value=True):
            result = coach_tools.save_activity_note("i123", "Felt strong", client)

        client.post_activity_message.assert_called_once_with("i123", "Felt strong")
        self.assertTrue(result["local_saved"])
        self.assertTrue(result["intervals_icu_updated"])


if __name__ == "__main__":
    unittest.main()