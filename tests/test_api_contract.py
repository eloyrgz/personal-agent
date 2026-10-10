import asyncio
import os
import sys
import unittest
from datetime import date
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import httpx


TRAINING_COACH_DIR = Path(__file__).resolve().parents[1] / "training-coach-agent"
sys.path.insert(0, str(TRAINING_COACH_DIR))

import api
import weekly_recap_runner
from weekly_recap_email import build_weekly_recap_email, summarize_activities


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
            patch.object(api.db_memory, "get_activities_in_range", return_value=[]) as get_activities,
            patch.object(api.db_memory, "get_weekly_load_trend", return_value=[]) as get_trend,
            patch.object(api.db_memory, "get_daily_wellness", return_value={}) as get_wellness,
            patch.object(api.db_memory, "set_weekly_recap_content") as save_content,
            patch.object(api.db_memory, "complete_weekly_recap", return_value=True),
            patch.object(api.db_memory, "fail_weekly_recap") as fail_recap,
            patch.object(api, "run_weekly_recap_agent", return_value={
                "summary_heading": "Semana constante",
                "summary": "Semana sólida.",
                "key_findings": [{"title": "Carga estable", "detail": "Sin cambios relevantes."}],
                "recommendations": [
                    {"category": "RECUPERACIÓN", "title": "Mantener la recuperación", "detail": "Evita aumentar carga esta semana."},
                    {"category": "ENTRENAMIENTO", "title": "Entrenar fácil", "detail": "Prioriza el ritmo conversacional."},
                ],
            }) as generate,
            patch.object(api, "build_weekly_recap_email", return_value=("<html>recap</html>", "recap text")),
            patch.object(api, "_send_weekly_recap_email", return_value="email-123") as send_email,
        ):
            response = asyncio.run(exercise_request())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "sent")
        self.assertEqual(response.json()["week_end"], "2026-10-04")
        generate.assert_called_once()
        get_activities.assert_called_once()
        get_trend.assert_called_once()
        get_wellness.assert_called_once()
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

    def test_weekly_recap_runner_selects_week_at_sunday_cutoff(self):
        self.assertEqual(
            weekly_recap_runner.week_start_for_recap(
                datetime.fromisoformat("2026-10-08T12:00:00+02:00")
            ),
            date.fromisoformat("2026-09-28"),
        )
        self.assertEqual(
            weekly_recap_runner.week_start_for_recap(
                datetime.fromisoformat("2026-10-11T21:00:00+02:00")
            ),
            date.fromisoformat("2026-10-05"),
        )

    def test_weekly_recap_rejects_current_week_before_sunday_cutoff(self):
        with self.assertRaises(api.HTTPException):
            api._validate_recap_week(
                date.fromisoformat("2026-10-05"),
                datetime.fromisoformat("2026-10-11T20:59:00+02:00"),
            )

    def test_weekly_recap_accepts_current_week_at_sunday_cutoff(self):
        self.assertEqual(
            api._validate_recap_week(
                date.fromisoformat("2026-10-05"),
                datetime.fromisoformat("2026-10-11T21:00:00+02:00"),
            ),
            date.fromisoformat("2026-10-11"),
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
                "Recap text",
                "<strong>Semana</strong>",
            )

        self.assertEqual(message_id, "email-123")
        request = resend_post.call_args.kwargs
        self.assertEqual(request["headers"]["Idempotency-Key"], "weekly-recap-2026-09-28")
        self.assertEqual(request["json"]["subject"], "Resumen de entrenamiento: Semana 40")
        self.assertEqual(request["json"]["html"], "<strong>Semana</strong>")
        self.assertEqual(request["json"]["text"], "Recap text")

    def test_weekly_recap_email_has_structured_workout_and_health_sections(self):
        activities = [{
            "activity_date": "2026-10-05T18:30:00",
            "activity_name": "Morning Run <tempo>",
            "activity_type": "Run",
            "moving_time_seconds": 17040,
            "distance_meters": 9800,
            "elevation_gain_meters": 110,
            "calories": 797,
            "feel": 4,
            "rpe": 7,
            "icu_load": 340,
            "average_hr": 150,
        }, {
            "activity_date": "2026-10-09T10:00:00",
            "activity_name": "Ruta endurance",
            "activity_type": "Ride",
            "moving_time_seconds": 6780,
            "distance_meters": 31000,
            "elevation_gain_meters": 635,
            "calories": 3121,
            "icu_load": 73,
        }]
        summary = summarize_activities(activities)
        html_content, text_content = build_weekly_recap_email(
            date.fromisoformat("2026-10-05"),
            date.fromisoformat("2026-10-11"),
            activities,
            summary,
            {
                "summary_heading": "Carga estable, buen descanso",
                "summary": "Carga consistente.",
                "key_findings": [
                    {"title": "Sueño mejorando", "detail": "La puntuación se mantiene estable."},
                    {"title": "Carga <controlada>", "detail": "Sin aumento significativo."},
                ],
                "recommendations": [
                    {"category": "RECUPERACIÓN", "title": "Priorizar el descanso", "detail": "Mantén una rutina regular."},
                    {"category": "ENTRENAMIENTO", "title": "Mantener sesiones fáciles", "detail": "Entrena a ritmo conversacional."},
                    {"category": "VIGILAR", "title": "Controlar pulso", "detail": "Observa su evolución."},
                ],
            },
            {"2026-10-05": {
                "steps": 8450,
                "weight_kg": 72.4,
                "resting_hr": 49,
                "sleep_seconds": 25200,
                "sleep_score": 82,
                "avg_sleep_hr": 45,
            }, "2026-10-11": {"resting_hr": 52}},
            [
                {"week_start": "2026-09-28", "last_ctl": 31.7},
                {
                    "week_start": "2026-10-05",
                    "last_ctl": 36,
                    "last_atl": 55,
                    "last_tsb": -19,
                },
            ],
        )

        self.assertIn("lang='es'", html_content)
        self.assertIn("TRAINING COACH · INFORME SEMANAL", html_content)
        self.assertIn(">Resumen de entrenamiento</div>", html_content)
        self.assertIn("05 OCT – 11 OCT · Semana 41", html_content)
        self.assertIn("Tiempo total", html_content)
        self.assertIn("6 h 37 min", html_content)
        self.assertIn("Distancia · todas las actividades", html_content)
        self.assertNotIn("Distancia corriendo", html_content)
        self.assertIn("40,8 km", html_content)
        metrics_html = html_content.split("Actividad semanal", 1)[0]
        self.assertNotIn("9,8 km", metrics_html)
        self.assertIn("Carga semanal", html_content)
        self.assertNotIn("Carga total", html_content)
        self.assertIn("data-icon='chart'", html_content)
        self.assertIn("413", html_content)
        self.assertIn("Desnivel · todas las actividades", html_content)
        self.assertNotIn("Desnivel positivo</span>", html_content)
        self.assertIn("data-icon='mountain'", html_content)
        self.assertIn("745 m", html_content)
        self.assertIn("Actividad semanal", html_content)
        self.assertIn("Running", html_content)
        self.assertIn("Ciclismo", html_content)
        self.assertIn("Recuperación", html_content)
        self.assertIn("Sueño · Lunes", html_content)
        self.assertIn("7 h 00 min", html_content)
        self.assertIn("82/100", html_content)
        self.assertIn("52 bpm", html_content)
        self.assertIn("Forma (TSB)", html_content)
        self.assertIn("-19,0", html_content)
        self.assertIn("Evolución de la semana", html_content)
        self.assertIn("Puntuación del sueño · escala de 0 a 100", html_content)
        self.assertIn("Lo más importante", html_content)
        self.assertIn("Recomendaciones para la próxima semana", html_content)
        for icon in ("activity", "clock", "run", "bike", "moon", "heart"):
            self.assertIn(f"data-icon='{icon}'", html_content)
        self.assertIn("&#x25F7;", html_content)
        self.assertIn("&#x2197;", html_content)
        self.assertIn("&#x2661;", html_content)
        self.assertNotIn("<svg", html_content)
        self.assertNotIn("&#x1F", html_content)
        self.assertIn("Sueño mejorando", html_content)
        self.assertIn("&lt;controlada&gt;", html_content)
        self.assertIn("padding:11px;background:#1b3331;border-radius:8px", html_content)
        self.assertNotIn("border-left:3px solid", html_content)
        self.assertIn("★ RECUPERACIÓN · Priorizar el descanso", text_content)
        self.assertNotIn("PRIORIDAD", html_content)
        self.assertNotIn("PRIORIDAD", text_content)
        self.assertIn("ENTRENAMIENTO", html_content)
        recommendations_html = html_content.split("Recomendaciones para la próxima semana", 1)[1].split(
            "TRAINING COACH · Semana", 1
        )[0]
        self.assertNotIn("bgcolor='#142b2b'", recommendations_html)
        self.assertEqual(recommendations_html.count("background:#1b3331;border-radius:8px"), 1)
        self.assertNotIn("Objetivo para la próxima semana", html_content)
        self.assertNotIn("7–8 horas", html_content)
        self.assertNotIn("TENDENCIAS", text_content)
        self.assertIn("LO MÁS IMPORTANTE", text_content)
        self.assertIn("RECOMENDACIONES PARA LA PRÓXIMA SEMANA", text_content)
        self.assertIn("Carga <controlada>", text_content)
        self.assertIn("max-width:560px", html_content)
        self.assertNotIn("Diario de entrenamientos", html_content)
        self.assertNotIn("Gráficos diarios", html_content)
        self.assertNotIn("Morning Run", html_content)
        self.assertNotIn("<tempo>", html_content)
        self.assertIn("72.4 kg", text_content)
        self.assertIn("49 bpm", text_content)
        self.assertNotIn("Morning Run", text_content)
        self.assertIn("18:30 · Run <tempo>", text_content)
        self.assertIn("sueño 7 h 00 min", text_content)
        self.assertIn("puntuación 82", text_content)
        self.assertIn("FC media durante el sueño 45 bpm", text_content)
        self.assertIn("Carga estable, buen descanso", html_content)
        self.assertNotIn("Tu semana, en perspectiva", html_content)
        self.assertIn("pasos", text_content)
        self.assertNotIn("CTL estable.", text_content)
        self.assertLess(html_content.index("Recuperación"), html_content.index("Evolución de la semana"))
        self.assertLess(html_content.index("Evolución de la semana"), html_content.index("Lo más importante"))
        self.assertLess(html_content.index("Lo más importante"), html_content.index("Recomendaciones para la próxima semana"))

    def test_weekly_summary_drops_repeated_heading_and_date(self):
        html_content, text_content = build_weekly_recap_email(
            date.fromisoformat("2026-09-28"),
            date.fromisoformat("2026-10-04"),
            [],
            summarize_activities([]),
            {
                "summary_heading": "Carga equilibrada",
                "summary": "Resumen de la semana (28 sep 2026): carga estable y buena continuidad.",
                "key_findings": [{"title": "Sin cambios notables", "detail": "Carga equilibrada."}],
                "recommendations": [
                    {"category": "CARGA", "title": "Mantener la progresión", "detail": "Aumenta de forma gradual."},
                    {"category": "RECUPERACIÓN", "title": "Seguir descansando", "detail": "Mantén los hábitos actuales."},
                ],
            },
        )

        self.assertIn("Carga equilibrada", html_content)
        self.assertIn("carga estable y buena continuidad.", html_content)
        self.assertNotIn("Resumen de la semana (28 sep 2026)", html_content)
        self.assertIn("carga estable y buena continuidad.", text_content)

    def test_weekly_recap_sections_use_fallback_when_agent_items_are_missing(self):
        html_content, text_content = build_weekly_recap_email(
            date.fromisoformat("2026-09-28"),
            date.fromisoformat("2026-10-04"),
            [],
            summarize_activities([]),
            {"summary_heading": "Semana ligera", "summary": "Semana de recuperación."},
        )

        self.assertIn("Sin hallazgos destacados", html_content)
        self.assertIn("No hay datos suficientes para proponer acciones más específicas", html_content)
        self.assertIn("Sin hallazgos destacados", text_content)
        self.assertIn("No hay datos suficientes para proponer acciones más específicas", text_content)

    def test_activity_totals_are_grouped_by_type(self):
        summary = summarize_activities([
            {"activity_type": "Run", "moving_time_seconds": 3600, "distance_meters": 10000, "icu_load": 80},
            {"activity_type": "Trail Run", "moving_time_seconds": 1800, "distance_meters": 5000, "icu_load": 40},
            {"activity_type": "Road Cycling", "moving_time_seconds": 3600, "distance_meters": 30000, "icu_load": 60},
            {"activity_type": "Mountain Bike", "moving_time_seconds": 5400, "distance_meters": 25000, "icu_load": 70},
            {"activity_type": "Yoga", "moving_time_seconds": 1800, "distance_meters": 0, "icu_load": 15},
        ])

        by_type = {item["activity_type"]: item for item in summary["by_type"]}
        self.assertEqual(summary["sessions"], 5)
        self.assertEqual(summary["duration_seconds"], 16200)
        self.assertEqual(by_type["Running"]["sessions"], 2)
        self.assertEqual(by_type["Running"]["distance_meters"], 15000)
        self.assertEqual(by_type["Cycling"]["sessions"], 2)
        self.assertEqual(by_type["Cycling"]["training_load"], 130)
        self.assertIsNone(by_type["Yoga"]["distance_meters"])
        self.assertEqual(by_type["Yoga"]["training_load"], 15)


if __name__ == "__main__":
    unittest.main()