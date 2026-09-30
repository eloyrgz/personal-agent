import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


TRAINING_COACH_DIR = Path(__file__).resolve().parents[1] / "training-coach-agent"
sys.path.insert(0, str(TRAINING_COACH_DIR))

import api


class ApiLifecycleTests(unittest.TestCase):
    def test_lifespan_closes_both_memory_owners(self):
        coach_memory = Mock()
        close_injury_memory = Mock()

        async def exercise_lifespan():
            with (
                patch.object(api, "db_memory", coach_memory),
                patch.object(api, "close_db_memory", close_injury_memory),
            ):
                async with api.lifespan(api.app):
                    pass

        asyncio.run(exercise_lifespan())

        coach_memory.close.assert_called_once_with()
        close_injury_memory.assert_called_once_with()

    def test_injury_cleanup_runs_when_coach_close_fails(self):
        coach_memory = Mock()
        coach_memory.close.side_effect = RuntimeError("coach close failed")
        close_injury_memory = Mock()

        async def exercise_lifespan():
            with (
                patch.object(api, "db_memory", coach_memory),
                patch.object(api, "close_db_memory", close_injury_memory),
            ):
                async with api.lifespan(api.app):
                    pass

        with self.assertRaisesRegex(RuntimeError, "coach close failed"):
            asyncio.run(exercise_lifespan())

        close_injury_memory.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()