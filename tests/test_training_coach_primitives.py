import sys
import unittest
from pathlib import Path


TRAINING_COACH_DIR = Path(__file__).resolve().parents[1] / "training-coach-agent"
sys.path.insert(0, str(TRAINING_COACH_DIR))

from injury_agent import _extract_medical_history_signals, _extract_symptom_signals
from sync_pipeline import TrainingDataPipeline


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


if __name__ == "__main__":
    unittest.main()